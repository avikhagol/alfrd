import numpy as np
from pathlib import Path
import os
import subprocess, glob, shutil, time, json
from collections import defaultdict
import sys
from contextlib import contextmanager

@contextmanager
def padded_output(padding=4):
    """Context manager for padded output"""
    original_write = sys.stdout.write
    
    def padded_write(text):
        if text.strip():  # Only pad non-empty lines
            original_write(' ' * padding + text)
        else:
            original_write(text)
    
    sys.stdout.write = padded_write
    try:
        yield
    finally:
        sys.stdout.write = original_write

def read_inputfile(folder,inputfile='.inp'):
    """Read the input file and return a dictionary with the parameters.

    Returns
    ---

    (params, files, input_folder)

    """
    params = defaultdict(list)
    input_folder= None
    files=glob.glob(f'{folder}/*{inputfile}',recursive=True)
    if not files: files=glob.glob(f'{folder}/*/*{inputfile}',recursive=False)
    if not files: files=glob.glob(f'{folder}/*/*/*{inputfile}',recursive=False)
    if files:
        input_folder = str(Path(files[-1]).parent) + '/'
        for filepath in files:
            if inputfile in filepath:
                with open(filepath,'r') as f:
                    pr=f.read().splitlines()
                    for p in pr:
                        if '#' in p:
                            continue
                        elif '=' in p:
                            k,v=p.split('=')
                            # check for leading zeros
                            if str(v).strip() and v.strip()[0] == '0':
                                v   =   str(v).strip()
                                
                            else:
                                try:
                                    v=int(v)
                                except:
                                    try:
                                        v=float(v)
                                    except:
                                        v=str(v).strip()
                                        if "*" in v:
                                            try:
                                                v = glob.glob(f'{v}', recursive=True)
                                            except:
                                                v = str(v)
                                        else:
                                            v = v.lower() == 'true' if (any(boolv == v.lower() for boolv in ['true', 'false'])) else v
                            params[k.strip()]=v
                            
    return params, files, input_folder

def update_existing_dict(to, from_dic):
    """_updates values only for existing keys_

    Args:
        to (_type_): _description_
        from_dic (_type_): _description_

    Returns:
        _type_: _description_
    """
    for k,v in from_dic.items():
        if k in to:
            to[k]=from_dic[k]
    return to

def read_metafile(metafile):
    with open(metafile, 'r') as sf:
        metad                       =   sf.read()
        meta                        =   json.loads(metad)
    return meta

def find_size(fitsfile):
    size = np.round(Path(fitsfile).stat().st_size/(1024*1024),2)
    if size >= 1024.0 : 
        size = size/1024
        size = f"{np.round(size, 2)} GB"
    else:
        size = f"{np.round(size, 2)} MB"
    return size

def find_project(fitsfile):
    project     =   str(Path(fitsfile).parent.name)
    return str(project)

def build_path(filepath):
    """
    This builds new file path by adding _{n+1} if there exists one with similar name,
    """
    opt = filepath
    if Path(filepath).exists():
        numb = 1
        while Path(filepath).exists():
            filepath = "{0}_{2}{1}".format(
                *(Path(opt).parent / Path(opt).stem, Path(opt).suffix,numb))
            try :
                if Path(filepath).exists():
                    numb += 1 
            except:
                pass
    return filepath

def symlink_bywd(wd, fitsfile, create=True):
    rawsymlink          =   f"{wd}/raw/{Path(fitsfile).name}" 
    Path(f"{wd}/raw").mkdir(parents=True, exist_ok=True)
    if create : os.symlink(fitsfile, rawsymlink)
    return rawsymlink

def dir_for_project(fitsfile, tdir='/data/avi/reductions/100test/', ifolder='/data/avi/gh/picard/src/picard/input_template/', create=True, splitted=False):
    """
    looks for wd using project name
    """
    new                 =   True
    segment             =   find_project(fitsfile)
    wd                  =   f"{tdir}{segment}/wd"
    wd_ifolder          =   None
    lookfile            =   fitsfile if not splitted else f"{str(Path(fitsfile).stem)}_split{Path(fitsfile).suffix}"
    if create:
        if not Path(f"{wd}/raw/{Path(lookfile).name}").exists():
            wd                  =   build_path(wd)
            
        try:
            rawsymlink = symlink_bywd(wd, fitsfile)
               
            wd_ifolder          =   f'{wd}/input_template/'
            if not Path(wd_ifolder).exists():shutil.copytree(ifolder,wd_ifolder)
        except Exception as e:
            print(f"exists? : {segment} : {e}")
            new             =   False
    
        new             =   False
    else:
        possible_file = glob.glob(f'{wd}*/raw/{Path(lookfile).name}')
        
        if len(possible_file):
            wd_ifolder = Path(possible_file[0]).parent.parent / "input_template"
            new = False
    return wd_ifolder, new

def del_extra_wdfolder(wd_ifolder, fitsfile):
    """
    useful when for a project name there are more folders created by mistakes,
    e.g wd_1/ wd_2/ instead of just wd/

    Note: assumes wd_1, wd_2 etc are created by mistake
    """
    if 'wd_' in str(wd_ifolder):
        subprocess.run(['rm','-rf', str(Path(wd_ifolder).parent.parent)])
        wd_ifolder, new= dir_for_project(fitsfile)
        print('created',wd_ifolder)

def latest_file(path: Path, pattern: str = "*"):
    """
    to get the last file that was generated, this can be useful for getting any new logfiles.
    """
    files = path.glob(pattern)
    lastf = Path('')
    
    try:
        lastf = max(files, key=lambda x: x.stat().st_ctime)
    finally:
        return lastf
    
def timeinmin(td):
    """
    convert timdedelta in XXmYYs format
    """
    tdm,tds     =   0,0
    if td>=60:
        tdm     =   td//60
        tds     =   td%60
    else:
        tds     =   td
    ret_time    =   f"{int(tdm)}m{np.round(tds,1)}s"

    return ret_time

def del_fl(wd, count=0, fl='*ms*', rm=False):
    """
    delete files from the input folder
    """
    wd          =   Path(wd)
    filefound = glob.glob(f"{str(wd)}/{fl}")
    if len(filefound):
        cmd = ['rm','-rf']
        cmd.extend(str( wd / " ".join(filefound)).split(' '))
        print(" ".join(cmd))
        count+=1
        if rm: subprocess.run(cmd)
    return count

def build_logpath(wd_ifolder):
    """
    builds rpicard and casa style log files for the current time
    """
    thisdate = time.strftime('%F-%T', time.gmtime())
    thisdate = thisdate.replace(':', '_')
    
    errlogf = Path(wd_ifolder).parent / f'mpi_and_err.out_{thisdate}'
    casalogf = Path(wd_ifolder).parent / f'casa.log_{thisdate}'
    
    return str(errlogf), str(casalogf)