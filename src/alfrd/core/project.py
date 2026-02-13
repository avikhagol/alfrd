from alfrd import ALFRD_DIR, PROJ_DIR, REGISTERED_STEPS, VALIDATORS, VALIDATE_AFTER, VALIDATE_BEFORE
from pathlib import Path
from alfrd.core.logger import livelogger
import importlib
import traceback
import shutil
import copy
from collections import defaultdict
import yaml

CONFIGFILE              =   "config.yaml"


class ProjectConfiguration:
    def __init__(self, thisproject):
        self.thisproject            =   thisproject
        self.fnname                 =   None
        self.configfile             =   Path(self.thisproject.get_projdir()) / CONFIGFILE
        
        self.debug                  =   False
        
    def func(self, name):
        self.fnname =    name
        return self
    
    def validate_before(self, *validatornames):
        self.update_validation_functions(VALIDATE_BEFORE, validatornames)
    
    def validate_after(self, *validatornames):
        self.update_validation_functions(VALIDATE_AFTER, validatornames)
        
    
    def update_validation_functions(self, dict_validation, validatornames):
        """checks and finds the function for the validator names provided, adds a warning in the log if name doesn't exist

        Args:
            dict_validation (Dict): the dictionary of validator which runs before/after the registered functions
            validatornames (list): name of the validators as a list of strings

        Returns:
            Dict: returns the updated dictionary
        """
        validatornames      =   list(validatornames)
        givenvalidators     =   copy.deepcopy(validatornames)
        failedvalidatornames=   []
        for i,validator_name in enumerate(givenvalidators):
            if validator_name in VALIDATORS:
                validatornames[i] = VALIDATORS[validator_name]['function']
            else:
                failedvalidatornames.append(validator_name)
        
        if failedvalidatornames: 
            livelogger.log(f"These validators don't exist : {failedvalidatornames}", level="WARN")
            for failedvalidatorname in failedvalidatornames:
                validatornames.remove(failedvalidatorname)
                
        if not self.fnname in dict_validation:
            dict_validation[self.fnname]            =   {'functions': []}
        
        existing_validators =   set(dict_validation[self.fnname]['functions'])
        
        if all(validatorname in existing_validators for validatorname in validatornames):           # to avoid running update since the validator already exists
            return dict_validation
        
        existing_validators.update(validatornames)                                                  # updates the global dictionary and adds a log in the following lines.
        dict_validation[self.fnname]['functions']   =   list(existing_validators)
        livelogger.log(f"Updated! {dict_validation}")
        return dict_validation
        
    def add_param(self, **kwargs):
        for key,value in kwargs.items():
            if key not in REGISTERED_STEPS[self.fnname]:
                REGISTERED_STEPS[self.fnname][key]  =   value
                livelogger.log(f"param for {self.fnname} added {key}={value}")
    
    def edit(self, **kwargs):
        for key,value in kwargs.items():
            if key in REGISTERED_STEPS[self.fnname]:
                REGISTERED_STEPS[self.fnname].update({key: value})
                livelogger.log(f"param for {self.fnname} updated to {key}={value}")
    
    def save(self):
        ddic                =   self.cleandata_foryaml(self.thisproject.get_functions())
        with open(self.configfile, "w") as configbuff:
            datayaml        =   yaml.dump(ddic, Dumper=yaml.SafeDumper)
            configbuff.write(datayaml)
        livelogger.log(f"saved {self.configfile}")
        
    def load(self):
        loadeddic_data                  =   None
        if self.debug: livelogger.log(f"reading {self.configfile}", level="DEBUG")
        with open(self.configfile, "r") as configbuff:
            loadeddic_data              =   yaml.load(configbuff.read(), Loader=yaml.SafeLoader)
        if self.debug: livelogger.log(f"loaded {self.configfile} : {loadeddic_data}", level="DEBUG")
        
        loadeddic_data                  =   self.parse_cleaneddata_fromyaml(loadeddic_data)
        livelogger.log(f"loading.. {self.configfile}")
        
        for key, category in self.thisproject.get_functions().items():
            if key in loadeddic_data:
                category.update(loadeddic_data[key])
                livelogger.log(f"loaded {key} : {category}")
            
    def parse_cleaneddata_fromyaml(self, loadeddic_data):
        if loadeddic_data:
            categories = self.thisproject.get_functions()
            
            fnobjs = {}
            for _, categorydict in categories.items():
                for fnname, fnparams in categorydict.items():
                    if 'function' in fnparams and not isinstance(fnparams['function'], str):
                        fnobjs[fnname]  =   fnparams['function']
            if fnobjs:
                for loadedcategory_name, loadedcategory_dict in loadeddic_data.items():
                    for funcname, funcparams in loadedcategory_dict.items():
                        for fky in funcparams:
                            if 'function' in fky:
                                if isinstance(funcparams[fky], str):
                                    loadeddic_data[loadedcategory_name][funcname][fky]               =   fnobjs[funcparams[fky]]
                                elif isinstance(funcparams[fky], list):
                                    for i, funclname in enumerate(funcparams[fky]):
                                        if isinstance(funclname, str):
                                            loadeddic_data[loadedcategory_name][funclname][fky][i]    =   fnobjs[funclname]
            else:
                livelogger.log(f"could not map function objects to the function name", level="SEVERE")
                                            
            for category_name, category_dictdefault in categories.items():
                if not category_name in loadeddic_data:
                    loadeddic_data[category_name] = category_dictdefault
            
        return loadeddic_data
        
    def cleandata_foryaml(self, data):
        cleaned_data        =   copy.deepcopy(data)
        if isinstance(cleaned_data, dict):
            for key,value in cleaned_data.items():
                if isinstance(value, dict):
                    for ky,val in value.items():
                        if ky not in cleaned_data:
                            cleaned_data[key][ky] = val
                        for k,v in val.items():
                            if 'function' in k:
                                if not isinstance(cleaned_data[key][ky][k], str):
                                    if isinstance(cleaned_data[key][ky][k], list):
                                        cleaned_data[key][ky][k]    =   [fname.__name__ if not isinstance(fname, str) else fname for fname in cleaned_data[key][ky][k]]
                                    else:
                                        cleaned_data[key][ky][k]    =   cleaned_data[key][ky][k].__name__
        return cleaned_data
        

class Project:
    def __init__(self, name="", use_symlink=True, verbose=True):
        self.name                           =   name
        self.use_symlink                    =   use_symlink
        self.verbose                        =   verbose
            
    def get_projdir(self, create=False):
        proj_dir                            =   Path(f"{PROJ_DIR}/{self.name}")
        if create:
           Path(proj_dir).mkdir(parents=True,exist_ok=True)
           livelogger.log(f"project {self.name} created!", level="INFO")
        elif (not self.name) or (not proj_dir.exists()):
            livelogger.log(f"project {self.name} not found!", level="SEVERE")
            raise ModuleNotFoundError(f"Project '{str(self.name)}' not found!")                    
        return proj_dir
        
    def create(self):
        self.get_projdir(create=True)
    
    def add(self, *paths):
        proj_dir                            =   self.get_projdir()
        for path in paths:
            if Path(path).exists():
                if Path(path).is_dir():
                    shutil.copytree(src=path, dst=f"{proj_dir}/{Path(path).name}", symlinks=self.use_symlink)
                else:
                    if self.use_symlink:
                        Path(f"{proj_dir}/{Path(path).name}").symlink_to(f"{Path(path).absolute()}")
                    else:
                        shutil.copyfile(src=path, dst=f"{proj_dir}/{Path(path).name}", follow_symlinks=False)
                livelogger.log(f"{path} is added to {self.name} with symlinks={self.use_symlink}")
                
            else:
                livelogger.log(f"{path} not found!", level="SEVERE")
                
    
    def rm(self):
        livelogger.log(f"removing project {self.name}")
        shutil.rmtree(self.get_projdir(), ignore_errors=True)
    
    def list_projects(self):
        projects                            =   [Path(projdir).name for projdir in Path(f"{PROJ_DIR}").glob("*") if Path(projdir).is_dir()]
        livelogger.log(f"found projects : {projects}")
        return projects
    
    def load_project(self):
        proj_dir                            =   self.get_projdir()
        if REGISTERED_STEPS or VALIDATORS:
            livelogger.log(f"Warning! previous project was not cleared.", level="WARN")
        if Path(proj_dir).is_dir():
            init_file                       =   proj_dir / "__init__.py"            
            module_name                     =   proj_dir.name if init_file.exists() else proj_dir.stem
            
            livelogger.log(f"loading project: {module_name}")
            try:
                for proj_file in proj_dir.glob("*.py"):
                    spec                    =   importlib.util.spec_from_file_location(module_name, proj_file)
                    module                  =   importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                if REGISTERED_STEPS:
                    livelogger.log(f"Registered functions: {list(REGISTERED_STEPS.keys())}")
                    livelogger.log(f"Validator functions: {list(VALIDATORS.keys())}")
            except ValueError as err:
                traceback.print_exc()
                livelogger.log(f"FAILED!", level="FAIL")
                
    def clear_project(self):
        """_clears functions loaded in the validators and registered catagories_
        """
        for catname, category in self.get_functions().items():
            category.clear()
            livelogger.log(f"{catname} cleared!")
            
    
    def get_functions(self):    
        return {'REGISTERED': REGISTERED_STEPS, 'VALIDATORS': VALIDATORS, 'VALIDATE_BEFORE': VALIDATE_BEFORE, 'VALIDATE_AFTER': VALIDATE_AFTER}
    
    def configure(self):
        if not any(catdic for catdic in self.get_functions().values()):
            livelogger.log(f"attempting to work without loading a project", level="SEVERE")
        return ProjectConfiguration(self)