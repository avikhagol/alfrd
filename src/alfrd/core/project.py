from alfrd import (
    REGISTERED_STEPS,
    VALIDATORS,
    VALIDATE_AFTER,
    VALIDATE_BEFORE,
    get_project_dir,
)
from pathlib import Path
import logging
from alfrd.plugins import _load_project_module, _purge_modules_from_directory
import shutil
import copy
from collections import defaultdict
import yaml
import sys

logger = logging.getLogger(__name__)

PROJ_CONFIGFILE                          =   ".alfrd_project.yaml"


def project_directory(project_name: str) -> Path:
    """Resolve one safe project directory strictly below ``ALFRD_HOME``."""
    candidate = Path(project_name)
    if (
        not project_name
        or candidate.is_absolute()
        or project_name in {".", ".."}
        or "/" in project_name
        or "\\" in project_name
        or len(candidate.parts) != 1
    ):
        raise ValueError(f"Invalid project name: {project_name!r}")

    root = get_project_dir().resolve()
    project_dir = (root / project_name).resolve()
    if project_dir.parent != root:
        raise ValueError(f"Invalid project name: {project_name!r}")
    return project_dir

class ProjectConfiguration:
    def __init__(self, thisproject):
        self.thisproject            =   thisproject
        self.fnname                 =   None
        self.configfile             =   Path(self.thisproject.get_projdir()) / PROJ_CONFIGFILE

        self.thisproject.configfile =   self.configfile
        
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
            validatornames  (list): name of the validators as a list of strings

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
            logger.warning("These validators don't exist: %s", failedvalidatornames)
            for failedvalidatorname in failedvalidatornames:
                validatornames.remove(failedvalidatorname)
                
        if not self.fnname in dict_validation:
            dict_validation[self.fnname]            =   {'functions': []}
        
        existing_validators =   set(dict_validation[self.fnname]['functions'])
        
        if all(validatorname in existing_validators for validatorname in validatornames):           # to avoid running update since the validator already exists
            return dict_validation
        
        existing_validators.update(validatornames)                                                  # updates the global dictionary and adds a log in the following lines.
        dict_validation[self.fnname]['functions']   =   list(existing_validators)
        logger.info("Updated validation configuration: %s", dict_validation)
        return dict_validation
        
    def add_param(self, **kwargs):
        for key,value in kwargs.items():
            if key not in REGISTERED_STEPS[self.fnname]:
                REGISTERED_STEPS[self.fnname][key]  =   value
                logger.info("param for %s added %s=%s", self.fnname, key, value)
    
    def edit(self, **kwargs):
        for key,value in kwargs.items():
            if key in REGISTERED_STEPS[self.fnname]:
                REGISTERED_STEPS[self.fnname].update({key: value})
                logger.info("param for %s updated to %s=%s", self.fnname, key, value)
    
    def save(self):
        ddic                =   self.cleandata_foryaml(self.thisproject.get_functions())
        with open(self.configfile, "w") as configbuff:
            datayaml        =   yaml.dump(ddic, Dumper=yaml.SafeDumper)
            configbuff.write(datayaml)
        logger.info("saved %s", self.configfile)

    def save_db(self):
        from alfrd.gui import create_app
        from alfrd.gui.model.tables import create_project_row, ProjectDB
        from alfrd.gui.model import db
        application = create_app()
        with application.app_context():
            row = create_project_row(self.thisproject, self)
            existing_proj = db.session.query(ProjectDB).filter_by(name=self.thisproject.name).first()
            if existing_proj:
                for column in ProjectDB.__table__.columns:
                    if column.primary_key:# or column.name == 'name'
                        continue

                    newval = getattr(row, column.name)
                    setattr(existing_proj, column.name, newval)
                db.session.commit()
                logger.info("Project '%s' updated in the database.", self.thisproject.name)
            else:
                db.session.add(row)
                db.session.commit()
                logger.info("Project '%s' added to the database.", self.thisproject.name)
        
    def load(self):
        loadeddic_data                  =   None
        if self.debug: logger.debug("reading %s", self.configfile)
        with open(self.configfile, "r") as configbuff:
            loadeddic_data              =   yaml.load(configbuff.read(), Loader=yaml.SafeLoader)
        if self.debug: logger.debug("loaded %s: %s", self.configfile, loadeddic_data)
        
        loadeddic_data                  =   self.parse_cleaneddata_fromyaml(loadeddic_data)
        logger.info("loading %s", self.configfile)
        
        for key, category in self.thisproject.get_functions().items():
            if key in loadeddic_data:
                category.update(loadeddic_data[key])
                logger.info("loaded %s: %s", key, category)
            
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
                logger.error("could not map function objects to the function name")
                                            
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
        self.desc                           =   ""
            
    def get_projdir(self, create=False):
        proj_dir                            =   project_directory(self.name)

        if create:
           Path(proj_dir).mkdir(parents=True,exist_ok=True)
           logger.info("project %s created!", self.name)
        elif (not self.name) or (not proj_dir.exists()):
            logger.error("project %s not found!", self.name)
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
                logger.info("%s is added to %s with symlinks=%s", path, self.name, self.use_symlink)
                
            else:
                logger.error("%s not found!", path)
                
    
    def rm(self):
        logger.info("removing project %s", self.name)
        shutil.rmtree(self.get_projdir(), ignore_errors=True)
    
    def list_projects(self):
        projects                            =   sorted(projdir.name for projdir in get_project_dir().glob("*") if projdir.is_dir())
        logger.info("found projects: %s", projects)
        return projects
    
    def load_project(self, purge_existing=False):
        """loads the modules from within the added project.
        Args:
            purge_existing (bool, optional): if True, clears existing modules that matches name with the one in the project being loaded. Defaults to False.
        """
        proj_dir                            =   self.get_projdir()
        if REGISTERED_STEPS or VALIDATORS:
            logger.warning("previous project was not cleared")
        if proj_dir.is_dir():
            logger.info("loading project: %s", self.name)
            path_entry = str(proj_dir)
            added_to_path = path_entry not in sys.path
            if added_to_path:
                sys.path.insert(0, path_entry)
            try:
                if purge_existing:
                    _purge_modules_from_directory(proj_dir)
                for proj_file in proj_dir.glob("*.py"):
                    logger.info("reading %s", proj_file)
                    _load_project_module(proj_file)
                    
                if REGISTERED_STEPS:
                    logger.info("Registered functions: %s", list(REGISTERED_STEPS))
                    logger.info("Validator functions: %s", list(VALIDATORS))
            except ValueError:
                logger.exception("failed to load project %s", self.name)
            finally:
                if added_to_path and path_entry in sys.path:
                    sys.path.remove(path_entry)
                
    def clear_project(self):
        """_clears functions loaded in the validators and registered catagories_
        """
        for catname, category in self.get_functions().items():
            category.clear()
            logger.info("%s cleared!", catname)
            
    
    def get_functions(self):    
        return {'REGISTERED': REGISTERED_STEPS, 'VALIDATORS': VALIDATORS, 'VALIDATE_BEFORE': VALIDATE_BEFORE, 'VALIDATE_AFTER': VALIDATE_AFTER}
    
    def configure(self):
        if not any(catdic for catdic in self.get_functions().values()):
            logger.error("attempting to work without loading a project")
        return ProjectConfiguration(self)