from pathlib import Path

import inspect
from typing import Callable, Dict, List
from functools import wraps
import typer
from alfrd.util import update_existing_dict, padded_output
import traceback

REGISTERED_STEPS: Dict[str, Dict[str, str]] = {}
VALIDATE_BEFORE: Dict[str, Dict[str, List[str]]] = {}
VALIDATE_AFTER: Dict[str, Dict[str, List[str]]] = {}
VALIDATORS: Dict[str, Dict[str, str]] = {}

def register(desc: str):
    """Decorator to register a pipeline step with required parameters."""
    def decorator(func: Callable):
        
        default_params, required_params = {},[]
        name = func.__name__
        signature   =   inspect.signature(func)

        for k, v in signature.parameters.items():
            if v.default is not inspect.Parameter.empty:
                default_params[k]   =   v.default
            else:
                required_params.append(k)

        if name in REGISTERED_STEPS:
            raise ValueError(f"Step with name '{name}' already registered!")
        REGISTERED_STEPS[name] = {"desc": desc, "function": func, "default_params": default_params,
                                  "required_params": required_params}
        return func
    return decorator

def validate(by: List[str]):
    """Decorator to validate a pipeline step."""
    by = [_by.__name__ if callable(_by) else _by for _by in by]    
    def wrapper(func: Callable):        
        step_name           =   func.__name__  # Using the function's name as step name
        if step_name not in REGISTERED_STEPS:
            raise ValueError(f"Step with name '{step_name}' not registered!")
        
        if step_name not in VALIDATE_BEFORE:VALIDATE_BEFORE[step_name] = {'functions':[]}
        if step_name not in VALIDATE_AFTER:VALIDATE_AFTER[step_name] = {'functions':[]}

        for val_name in by:            
            if val_name not in VALIDATORS:
                raise ValueError(f"Validator with name '{val_name}' does not exist!")

            # Append the function to the validation list
            if not VALIDATORS[val_name]['after']:
                VALIDATE_BEFORE[step_name]["functions"].append(VALIDATORS[val_name]['function'])
            else:
                VALIDATE_AFTER[step_name]["functions"].append(VALIDATORS[val_name]['function'])
        return func
    return wrapper

def validator(desc: str, after: bool = False, run_once: bool = False):
    """Decorator to register a validator for pipeline steps."""
    def decorator(func: Callable):
        default_params, required_params = {},[]
        name = func.__name__
        signature   =   inspect.signature(func)

        for k, v in signature.parameters.items():
            if v.default is not inspect.Parameter.empty:
                default_params[k]   =   v.default
            else:
                required_params.append(k)
        
        if name in VALIDATORS:
            raise ValueError(f"Validator with name '{name}' already registered!")
        VALIDATORS[name]  =   {"desc": desc, "function": func, "after": after,
                               "default_params":default_params,
                                "required_params":required_params,
                                "run_once":run_once, 'run_count':0,
                                }
        return func
    return decorator

def load_projects(project_dir: str):
    """Load projects from a specified directory."""
    project_dir_path = Path(project_dir)
    import importlib.util
    # Check if the project directory exists, if not, create it
    if not project_dir_path.exists():
        print(f"project directory '{project_dir}' does not exist. Creating it...")
        project_dir_path.mkdir(parents=True)
    
    # Loop through all Python files in the project directory
    for project_path in project_dir_path.glob("*.py"):
        module_name = project_path.stem
        
        spec = importlib.util.spec_from_file_location(module_name, project_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

    if not REGISTERED_STEPS:
        print("No steps found. Add projects to the projects directory.")

def iterate_over_lst(lst):
    """Decorator to apply a function to each element in lst."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            results = []
            for elem in lst:
                kwargs["elem"] = elem  # Pass the element as a keyword argument
                result = func(*args, **kwargs)  # Call the function
                results.append(result)
            return results  # Return all results
        return wrapper
    return decorator

class PipelineRun:
    def __init__(self):
        self.params                 =   {}
        self.project_name           =   ''
        self.step_name              =   ''
        self.registered_steps       =   {}
        self.validate_steps         =   {}
        self.validate_once          =   False
        self.prev_step_success      =   None
        self.validation_success     =   None

    def init_params(self, params):
        self.params                 =   {**params, **self.params}

    def update_params(self, params):
        """_adds new params, if already exists then updates_

        Args:
            params (_dict_): _provided parameters_
        """
        self.params = {**self.params, **params}             

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
            raise typer.Exit()
        else:
            if default_params:    update_existing_dict(default_params,self.params)
            
            required_params     =   {k: self.params.get(k, None) for k in required_params}
            default_params      =   {**default_params, **required_params}
            
        return default_params
        
    def run_step(self):
        result                              =   None
        step                                =   REGISTERED_STEPS[self.step_name]
        default_params                      =   step.get("default_params", {})
        required_params                     =   step.get("required_params", [])
        
        step_params                         =   self.all_step_params(required_params=required_params, default_params=default_params)
        
        func                                =   step["function"]
        
        try:
            result                          =   func(**step_params) if len(step_params) else func()
            self.prev_step_success          =   True
        except Exception as e:
            self.prev_step_success          =   False
            result                          =   str(e)
            typer.secho(f"Failed! {e}", fg=typer.colors.RED)
            traceback.print_exc()
            raise typer.Exit()
        self.params['ret']                  =   result

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
                            result                  =   validator_func(**validator_params) if len(validator_params) else validator_func()
                        
                        if self.validation_success is None: self.validation_success = result
                        VALIDATORS[validator_name]['run_count'] += 1
                        self.params['ret_valid']        =   result
                    except ValueError as e:
                        self.prev_step_success  =   False
                        typer.secho(f"Validation Failed! {e}", fg=typer.colors.RED)
                        result                  =   str(e)
                        traceback.print_exc()
                        raise typer.Exit()
                