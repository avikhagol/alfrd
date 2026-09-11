import hashlib
import importlib.util
import inspect
import sys
from functools import wraps
from pathlib import Path
from typing import Callable, Dict, List

import typer

REGISTERED_STEPS: Dict[str, Dict[str, str]] = {}
VALIDATE_BEFORE: Dict[str, Dict[str, List[str]]] = {}
VALIDATE_AFTER: Dict[str, Dict[str, List[str]]] = {}
VALIDATORS: Dict[str, Dict[str, str]] = {}                  # choice of name VALIDATORS because, the function returns bool viz, used for the code to proceed.


def _module_is_below(module, directory: Path) -> bool:
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return False
    try:
        Path(module_file).absolute().relative_to(directory.absolute())
    except (OSError, ValueError):
        return False
    return True


def _purge_modules_from_directory(directory: Path) -> None:
    """Remove only modules that were loaded from ``directory``."""
    for name, module in list(sys.modules.items()):
        if _module_is_below(module, directory):
            sys.modules.pop(name, None)


def _load_project_module(project_path: Path) -> str:
    """Load one plugin under a deterministic project-and-file-scoped name."""
    project_path = project_path.absolute()
    project_digest = hashlib.sha256(str(project_path.parent).encode()).hexdigest()[:12]
    module_name = f"_alfrd_project_{project_digest}_{project_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, project_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load project module: {project_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module_name

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

    # Check if the project directory exists, if not, create it
    if not project_dir_path.exists():
        print(f"project directory '{project_dir}' does not exist. Creating it... {project_dir_path}")
        project_dir_path.mkdir(parents=True)
    
    path_entry = str(project_dir_path)
    added_to_path = path_entry not in sys.path
    if added_to_path:
        sys.path.insert(0, path_entry)
    try:
        # Loop through all Python files in the project directory
        for project_path in project_dir_path.glob("*.py"):
            _load_project_module(project_path)
    finally:
        if added_to_path and path_entry in sys.path:
            sys.path.remove(path_entry)

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


# from contextlib import contextmanager

# @contextmanager
# def capture_logs(step_name: str, step_params: List):
#     """Captures stdout and prints it with a clean header/footer."""
#     captured_output = io.StringIO()
#     original_stdout = sys.stdout
#     sys.stdout = captured_output
#     step_params_store = step_params.copy()
#     if not 'logfile_path' in step_params_store:
#         step_params_store['logfile_path'] = str(Path.cwd())
#     try:
#         yield
#     finally:
#         sys.stdout = original_stdout
#         logs = captured_output.getvalue().strip()
#         if logs:
#             # You can use typer.secho here if you want colors
#             print(f"\n--- [LOGS: {step_name}] ---")
#             print(f" ###################################### {step_params_store['logfile_path']} ")
#             print(logs)
#             print(f"--- [END LOGS] ---\n")

class PipelineRun:
    """Compatibility facade over :class:`alfrd.core.pipeline.PipelineCore`."""

    def __init__(self):
        self.params = {}
        self.project_name = ""
        self.step_name = ""
        self.registered_steps = {}
        self.validate_steps = {}
        self.validate_once = False
        self.prev_step_success = None
        self.validation_success = None
        self.last_result = None

    def init_params(self, params):
        self.params = {**params, **self.params}

    def update_params(self, params):
        self.params.update(params)

    def _core(self, sequence, *, include_validators=True):
        from alfrd.core.pipeline import PipelineContext, PipelineCore

        core = PipelineCore.from_legacy_registries(
            REGISTERED_STEPS,
            VALIDATE_BEFORE if include_validators else {},
            VALIDATE_AFTER if include_validators else {},
            VALIDATORS,
            sequence=sequence,
            context=PipelineContext(self.params),
        )
        # The legacy singleton exposes a directly mutable params dictionary.
        core.context.params = self.params
        return core

    def all_step_params(self, required_params, default_params):
        missing = [name for name in required_params if name not in self.params]
        if missing:
            print(f"Missing required parameters: {', '.join(missing)}")
            raise typer.Exit()
        return {
            **{
                name: self.params.get(name, value)
                for name, value in dict(default_params).items()
            },
            **{name: self.params[name] for name in required_params},
        }

    def run_step(self):
        core = self._core([self.step_name], include_validators=False)
        self.last_result = core.run(
            {"dataset_id": self.project_name or "legacy"}, params=self.params
        )
        step_result = self.last_result.datasets[0].steps[0]
        self.prev_step_success = step_result.success
        self.params["ret"] = step_result.value
        if not step_result.success:
            message = step_result.error.message if step_result.error else "unknown error"
            typer.secho(f"Failed! {message}", fg=typer.colors.RED)
            raise typer.Exit()

    def run_validations(self):
        from alfrd.core.pipeline import FunctionPipelineStepValidator

        functions = self.validate_steps.get(self.step_name, {}).get("functions", ())
        validators = []
        for function in functions:
            metadata = VALIDATORS[function.__name__]
            if metadata.get("run_once") and metadata.get("run_count", 0):
                continue
            print(f"• {function.__name__}")
            validators.append(
                FunctionPipelineStepValidator(
                    function,
                    description=metadata.get("desc", ""),
                    run_once=metadata.get("run_once", False),
                )
            )
        core = self._core([self.step_name], include_validators=False)
        try:
            results = core.validate(
                validators, qualified_name=self.step_name, params=self.params
            )
        except (TypeError, ValueError) as error:
            self.prev_step_success = False
            typer.secho(f"Validation Failed! {error}", fg=typer.colors.RED)
            raise typer.Exit() from error
        self.prev_step_success = True
        self.validation_success = all(result.success for result in results)
        self.params["ret_valid"] = results[-1].success if results else None
        for function in functions[: len(results)]:
            VALIDATORS[function.__name__]["run_count"] += 1