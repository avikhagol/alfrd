from src.alfrd.core.project import Project, PROJ_DIR
from pathlib import Path
from src.alfrd.core.logger import livelogger

def test_project_basic():
    """creates a project and checks all the functionso of the project:
        - create empty project
        - remove project
        - add tmpfolder with __init__.py to the empty project
        - add tmpfile with @register to the project
        - clear projects
        - load projects
        - check the globals
        - delete the project
    Returns:
        None
    """
    proj            =   Project("pyt", use_symlink=True)
    path_tmppyt     =   'tmp_pyt.py'
    path_tmphelper =   'tmp_helpers/'
    
    with open(path_tmppyt, "w") as tmppytbuff:
        tmppytbuff.write("from alfrd.plugins import register\n\n\n@register(desc='testing register func')\ndef registered_pyt(name='pyt'):\n\treturn f'hello {name}'\n")
    
    proj.create()
    assert f"{PROJ_DIR}/pyt" in str(proj.get_projdir())
    
    proj.rm()
    assert not Path(f"{PROJ_DIR}/pyt").exists()
    
    
    proj.create()
    assert f"{PROJ_DIR}/pyt" in str(proj.get_projdir())
    
    proj.add(path_tmppyt)
    assert Path(f"{str(proj.get_projdir())}/{path_tmppyt}").exists()
    
    proj.create()
    assert f"{PROJ_DIR}/pyt" in str(proj.get_projdir())
    
    proj.rm()
    assert not Path(f"{PROJ_DIR}/pyt").exists()
    
    proj.create()
    assert f"{PROJ_DIR}/pyt" in str(proj.get_projdir())
    
    Path(path_tmphelper).mkdir(exist_ok=True)
    with open(f"{path_tmphelper}/__init__.py", 'w') as intf:
        pass
       
    proj.add(path_tmppyt, path_tmphelper)
    assert f"{PROJ_DIR}/pyt/{path_tmppyt}" in f"{str(proj.get_projdir())}/{path_tmppyt}"
    assert Path(f"{str(proj.get_projdir())}/{path_tmppyt}").exists()
    assert Path(path_tmphelper).exists() and Path(f"{str(proj.get_projdir())}/{path_tmphelper}").exists()
    
    proj.clear_project()
    proj.load_project()    
    assert list(proj.get_functions()['REGISTERED'].keys())          ==  ['registered_pyt']
    assert list(proj.get_functions()['VALIDATORS'].keys())          ==  []
    assert list(proj.get_functions()['VALIDATE_AFTER'].keys())      ==  []
    assert list(proj.get_functions()['VALIDATE_BEFORE'].keys())     ==  []
    
    proj.rm()
    assert not Path(f"{PROJ_DIR}/pyt").exists()
    
    for path_del in [path_tmppyt] + list(Path(path_tmphelper).glob('*')):
        Path(path_del).unlink()
    Path(path_tmphelper).rmdir()