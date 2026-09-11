import logging
from pathlib import Path

import typer
import yaml

from alfrd import B, X, REGISTERED_STEPS, VALIDATE_AFTER, VALIDATE_BEFORE, VALIDATORS, c
from alfrd.core.project import Project
from alfrd.util import padded_output, read_inputfile, update_existing_dict

logger = logging.getLogger(__name__)

WORKFLOW_CONFGIFILE             =   ".alfrd_workflow.yaml"

class WorkflowConfig:
    def __init__(self, thisworkflow):
        self.thisworkflow       =   thisworkflow
        self.configfile         =   f"{thisworkflow.proj.get_projdir()}/{WORKFLOW_CONFGIFILE}"
        
    def set_sequence(self, *steps):
        self.thisworkflow.sequence.extend(list(steps))
        logger.info("updated workflow sequence: %s", ", ".join(self.thisworkflow.sequence))
    
    def add_param(self, key, value):
        if key in self.thisworkflow.__dict__:
            self.thisworkflow.data_dict[key] = value
        
    def save(self):
        data = {
            "params": self.thisworkflow.params,
            "sequence": self.thisworkflow.sequence,
        }
        config_path = Path(self.configfile)
        document = {}
        if config_path.exists():
            with config_path.open("r", encoding="utf-8") as configbuff:
                loaded = yaml.safe_load(configbuff) or {}
            if isinstance(loaded, dict):
                document = loaded
        if "params" in document or "sequence" in document:
            document = {self.thisworkflow.name: document}
        document[self.thisworkflow.name] = data
        temporary_path = config_path.with_suffix(config_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as configbuff:
            yaml.safe_dump(document, configbuff)
        temporary_path.replace(config_path)
        self.thisworkflow.data_dict[self.thisworkflow.name] = data
        logger.info("saved %s", self.configfile)
    
    def load(self):
        if Path(self.configfile).exists():
            with open(self.configfile, "r") as configbuff:
                datayaml    =   yaml.safe_load(configbuff) or {}
                if self.thisworkflow.name in datayaml:
                    datayaml = datayaml[self.thisworkflow.name]
                for k, v in datayaml.items():
                    if k=='proj':
                        logger.warning("Cannot update 'proj' from the workflow configuration file; ignoring it")
                    elif k in self.thisworkflow.__dict__:
                        setattr(self.thisworkflow, k, v)
                    else:
                        logger.warning("%s is unsupported in the workflow configuration; ignoring it", k)
                self.thisworkflow.data_dict[self.thisworkflow.name] = datayaml
                logger.info("loaded %s", self.configfile)
        else:
            logger.warning("%s not found", self.configfile)
        

class WorkflowManager:
    """
    To define the workflow and execute each functions.
    """
    def __init__(self, name, proj : Project = None):
        self.params                 =   {}
        self.name                   =   name
        self.proj                   =   proj
        self.project_name           =   proj.name if proj else ''
        self.step_to                =   ''
        
        self.step_name              =   ''
        self.registered_steps       =   {}
        self.validate_steps         =   {}
        self.validate_once          =   False
        self.prev_step_success      =   None
        self.validation_success     =   None
        self.sequence               =   []
        
        self.data_dict              =   {self.name: {'params': self.params, 'sequence': self.sequence}}

    def init_params(self, params: dict):
        self.params                 =   {**params, **self.params}
        logger.info("Initialized workflow parameters: %s", ", ".join(params))

    def update_params(self, params):
        """
        Adds new params, if already exists then updates

        Args:
            params (Dict): provided parameters
        """
        self.params = {**self.params, **params}
        logger.info("Updated workflow parameters: %s", ", ".join(self.params))

    def all_step_params(self, required_params, default_params):
        """updates the params by looking into the current global parameter space

        Args:
            default_params (_dict_): _the default params dictionary_
            required_params (_list_): _the required params list_

        Raises:
            typer.Exit: _if missing required parameters shows an error_

        Returns:
            _dict_: _updated dictionary_
        """
        missing_params          =   [p for p in required_params if p not in self.params]
        if missing_params:
            print(f"Missing required parameters: {', '.join(missing_params)}")
            logger.error("Missing required parameters: %s", ", ".join(missing_params))
            raise typer.Exit()
        else:
            if default_params:    update_existing_dict(default_params,self.params)
            
            required_params     =   {k: self.params.get(k, None) for k in required_params}
            default_params      =   {**default_params, **required_params}
        self.data_dict.update(default_params)
        return default_params
    
    def configure(self):
        return WorkflowConfig(self)
        
    def run_step(self):
        result                              =   None
        step                                =   REGISTERED_STEPS[self.step_name]
        default_params                      =   step.get("default_params", {})
        required_params                     =   step.get("required_params", [])
        
        step_params                         =   self.all_step_params(required_params=required_params, default_params=default_params)
        
        func                                =   step["function"]
        
        try:
            logger.info("Running %s(%s)", self.step_name, ", ".join(f"{k}={v}" for k, v in step_params.items()))
            result                          =   func(**step_params) if len(step_params) else func()
            self.prev_step_success          =   True
        except Exception as e:
            self.prev_step_success          =   False
            result                          =   str(e)
            logger.exception("Error in step '%s'", self.step_name)
            typer.secho(f"Failed! {e}", fg=typer.colors.RED)
            raise typer.Exit()
        self.params['ret']                  =   result
        logger.info("Finished %s with result: %s", self.step_name, result)

    def run_validations(self):
        result                                  =   None
        if self.step_name in self.validate_steps:
            this_step                           =   self.validate_steps[self.step_name]
            
            for validator_func in this_step["functions"]:
                
                validator_name                  =   validator_func.__name__
                run_count                       =   VALIDATORS[validator_name]['run_count']
                if not (run_count>0 and VALIDATORS[validator_name]['run_once']) and self.validation_success!=False:
                    try:
                        print(f"• {validator_name}")
                        required_params         =   VALIDATORS[validator_name]['required_params']       # taking from global.
                        default_params          =   VALIDATORS[validator_name]['default_params']
                        validator_params        =   self.all_step_params(required_params=required_params, default_params=default_params)
                        self.prev_step_success  =   True                                                # this will change if error is raised.
                        with padded_output(3):
                            logger.info("Running validation %s(%s)", validator_name, ", ".join(f"{k}={v}" for k, v in validator_params.items()))
                            result                  =   validator_func(**validator_params) if len(validator_params) else validator_func()
                        
                        if result is False:
                            self.validation_success = False
                        VALIDATORS[validator_name]['run_count'] += 1
                        self.params['ret_valid']        =   result
                        logger.info("Finished validation %s with result: %s", validator_name, result)
                    except ValueError as e:
                        self.prev_step_success  =   False
                        logger.exception("Validation failed for %s", validator_name)
                        typer.secho(f"Validation Failed! {e}", fg=typer.colors.RED)
                        result                  =   str(e)
                        raise typer.Exit()
                    
    def get_sequence(self, step_name):
        if step_name not in REGISTERED_STEPS:
            print(f"Step '{step_name}' not found!")
            raise typer.Exit()
        allsteps    =   self.sequence or list(REGISTERED_STEPS.keys())
        idx_from    =   allsteps.index(step_name)
        idx_to      =   len(allsteps) # idx_from+1  # FIXME: uncomment to run only one step.
        
        if self.step_to:
            if self.step_to not in allsteps:
                print(f"Step '{self.step_to}' not found!")
                raise typer.Exit()
            else:
                idx_from    =   allsteps.index(step_name)
                idx_to      =   allsteps.index(self.step_to)+1
                
        steps     =   allsteps[idx_from:idx_to]
        print("Following steps will be executed in the sequence:")
        print( f"{c['bc']}", "-", f"\n - ".join(steps),f"{c['x']}\n")
        return steps
        
    def run_sequence(self, step_name):
        """Run the selected legacy workflow through ``PipelineCore``."""
        from alfrd.core.pipeline import PipelineContext, PipelineCore

        _params_found = {}
        art = f"""
        ╔══════════════════════════════════════════════════════════════════╗
        ║{self.proj.name.upper():^66}║
        ╚══════════════════════════════════════════════════════════════════╝
        """
        print(art)
        logger.info("Starting workflow %s for project: %s", self.name, self.proj.name.upper())
        if self.params and len(self.params):
            original_params = self.params
            for param in original_params:
                if isinstance(param, str) and '=' not in param:
                    if Path(param).exists():
                        logger.info("Loading parameters from file: %s", param)
                        _params_found, _, _ = read_inputfile(Path(param).absolute().parent,Path(param).name)
                        self.update_params(_params_found)
            if not isinstance(original_params, dict):
                _params_found = {
                    param.split("=", 1)[0]: param.split("=", 1)[1]
                    for param in original_params
                    if isinstance(param, str) and '=' in param
                }
                self.params = {}
        self.update_params(_params_found)
        logger.info("Final workflow parameters: %s", ", ".join(self.params))

        steps = self.get_sequence(step_name)
        logger.info("Workflow sequence determined: %s", ", ".join(steps))
        core = PipelineCore.from_legacy_registries(
            REGISTERED_STEPS,
            VALIDATE_BEFORE,
            VALIDATE_AFTER,
            VALIDATORS,
            sequence=steps,
            context=PipelineContext(self.params),
        )
        core.context.params = self.params
        self.last_result = core.run(
            {"dataset_id": self.project_name or self.name}, params=self.params
        )
        results = self.last_result.datasets[0].steps
        self.prev_step_success = all(
            result.status in {"succeeded", "skipped"} for result in results
        )
        self.validation_success = not any(
            result.error and result.error.type == "ValidationError" for result in results
        )
        completed = [result for result in results if result.status != "skipped"]
        if completed:
            self.step_name = completed[-1].step_name
            self.params["ret"] = completed[-1].value
        for result in results:
            state = "finished" if result.success else result.status
            print(f"{B} {state:8}: {c['bc']}{result.step_name}{X}")
        return self.last_result
    


class Workflow(WorkflowManager):
    def __init__(self, name, proj: Project):
        super().__init__(name=name, proj=proj)
        
    def run(self, step_name):
        return self.run_sequence(step_name)