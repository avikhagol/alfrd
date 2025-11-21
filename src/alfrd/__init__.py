from pathlib import Path
import shutil, sys, os
import typer
from typing import Optional
from typing_extensions import Annotated
from alfrd.util import padded_output

if sys.version_info >= (3, 8):
    from importlib.metadata import version
else:
    from importlib_metadata import version  # For Python < 3.8 (requires `importlib-metadata` package)

__version__ = version("alfrd")

from alfrd.util import read_inputfile
from alfrd.plugins import load_projects, List, REGISTERED_STEPS, VALIDATE_BEFORE, VALIDATE_AFTER, VALIDATORS, PipelineRun


alfrd_cli = typer.Typer()
Pipeline = PipelineRun()

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    else:
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
except:
    pass

c = {
    "x": "\033[0m",   # Reset
    "b": "\033[1m",   # Bold
    "d": "\033[2m",   # Dim

    # Normal Colors
    "k": "\033[30m",  # Black
    "r": "\033[31m",  # Red
    "g": "\033[32m",  # Green
    "y": "\033[33m",  # Yellow
    "bl": "\033[34m", # Blue
    "m": "\033[35m",  # Magenta
    "c": "\033[36m",  # Cyan
    "w": "\033[37m",  # White

    # Bright Colors
    "bk": "\033[90m",  # Bright Black
    "br": "\033[91m",  # Bright Red
    "bg": "\033[92m",  # Bright Green
    "by": "\033[93m",  # Bright Yellow
    "bbl": "\033[94m", # Bright Blue
    "bm": "\033[95m",  # Bright Magenta
    "bc": "\033[96m",  # Bright Cyan
    "bw": "\033[97m",  # Bright White

    # Background Colors (Shortened Keys)
    "bk_": "\033[40m",  # Black BG
    "r_": "\033[41m",   # Red BG
    "g_": "\033[42m",   # Green BG
    "y_": "\033[43m",   # Yellow BG
    "bl_": "\033[44m",  # Blue BG
    "m_": "\033[45m",   # Magenta BG
    "c_": "\033[46m",   # Cyan BG
    "w_": "\033[47m",   # White BG
}
B = "\033[1m"
X = "\033[0m"

# The project/plugin directory
ALFRD_DIR = Path("~/.alfrd").expanduser()
PROJ_DIR = Path(f"{ALFRD_DIR}/projects")

def load_steps(prefix=""):
    for i, (name, info) in enumerate(REGISTERED_STEPS.items()):
        print(f"{prefix}- {i}\t {c['bc']}{name.ljust(20)}{c['x']}: {info['desc']}")

def list_steps(proj):
    """List all registered steps."""
    print(f"\n\t\t{c['c']}ALFRD ({__version__}){c['x']}\n")
    print(f"  Run following pipeline steps for {c['bc']}{proj.upper()}{c['x']}")
    if not REGISTERED_STEPS:
        print("No pipeline steps registered.")
    else:
        load_steps()

def proj_dir(proj, create=False):
    
    project_dir= Path(f"{PROJ_DIR}/{proj}")
    sys.path.insert(0, str(project_dir))
            
    if create:
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"Created project directory at {project_dir}")
    if not project_dir.exists():
        print(f"Project directory '{proj}' does not exist.")
        raise ValueError(f"Project {proj} not found.")    
    return project_dir
    
@alfrd_cli.command()
def init(proj: str):
    """Initialize the project-specific plugin directory."""
    project_dir= Path(f"{PROJ_DIR}/{proj}")
    if not project_dir.exists():
        _ = proj_dir(proj, create=True)
    else:
        print(f"Project directory already exists at {project_dir}")

@alfrd_cli.command()
def ls(proj: str):
    """List all available pipeline steps for a project."""
    project_dir = proj_dir(proj)
    
    load_projects(project_dir)
    list_steps(proj)
    
@alfrd_cli.command()
def lsp():
    """List all available projects."""
    project_dirs = list(Path(f"{PROJ_DIR}").glob("*"))
    
    if len(project_dirs):
        print(f"\tAvailable projects:")
        for proj in project_dirs:
            print(f"\t\t{proj.name}")
            try:
                load_projects(proj)
                load_steps(prefix="\t\t\t")
            except:
                print("\t\t\tsteps not configured properly!")
    else:
        print("no projects found!", )

@alfrd_cli.command()
def run(
    step_name: str                  =   typer.Argument(help="name of the step name in project"), 
    proj: str                       =   typer.Argument(help="name of the ALFRD project"), 
    step_to: str                    =   None,
    
    params: List[str]               =   typer.Argument(None, help="Key-value pairs of parameters or parameter file path (e.g., id=123 name=Test)"),
    steps : Optional[List[str]]     =   typer.Option(None, help="list of steps e.g., --steps=step1 --steps=step2 | supersedes values and sequence of the steps", 
                                                     show_default=False),
    ):
    """Run a specific pipeline step for a project."""    
    _params_found           =   {}
    
    art = f"""
    ╔══════════════════════════════════════════════════════════════════╗
    ║{proj.upper():^66}║
    ╚══════════════════════════════════════════════════════════════════╝
    """
    print(art)
    
    if params and len(params):
        for param in params:
            if not '=' in param:
                if Path(param).exists():
                    _params_found, _, _ = read_inputfile(Path(param).absolute().parent,Path(param).name)
                    Pipeline.update_params(_params_found)

        _params_found = {param.split("=")[0]: param.split("=")[1] for param in params if '=' in param}
    Pipeline.update_params(_params_found)
    
    project_dir             =   proj_dir(proj)                                          # Ensure project directory exists
    load_projects(project_dir)                                                          # Load all project steps
    
    if step_name not in REGISTERED_STEPS:
        print(f"Step '{step_name}' not found! Use `ls` to view available steps.")
        raise typer.Exit()
    allsteps    =   steps or list(REGISTERED_STEPS.keys())
    idx_from    =   allsteps.index(step_name)
    idx_to      =   idx_from+1
    
    if step_to:
        if step_to not in REGISTERED_STEPS:
            print(f"Step '{step_to}' not found! Use `ls` to view available steps.")
            raise typer.Exit()
        else:
            idx_from    =   allsteps.index(step_name)
            idx_to      =   allsteps.index(step_to)+1
            
    steps     =   allsteps[idx_from:idx_to]
    # print(allsteps[idx_from:idx_to])
    print("Following steps will be executed in the sequence:")
    print( f"{c['bc']}", "-", f"\n - ".join(steps),f"{c['x']}\n")
    nsteps = len(steps)
    for s,step_name in enumerate(steps):
        if s==0: 
            Pipeline.prev_step_success     =   True
            Pipeline.validation_success    =   True
        Pipeline.step_name                      =   step_name
        
        if Pipeline.prev_step_success and (step_name in VALIDATE_BEFORE) and VALIDATE_BEFORE[step_name]['functions']:      
            print(f"\n>  {B}Pre-processing{X} ({Pipeline.step_name})")
            print(f"""  ─────────────────────────────────────────────────────────────────""")
            with padded_output(4):
                Pipeline.validate_steps         =   VALIDATE_BEFORE
                Pipeline.run_validations()

        if Pipeline.prev_step_success and Pipeline.validation_success:
            print(f"\n>  {B}Processing{X}: {proj.upper()} {step_name}")
            print("""  ─────────────────────────────────────────────────────────────────""")
            with padded_output(4):
                Pipeline.run_step()
        
        # Run validations post run
        if Pipeline.validation_success and Pipeline.prev_step_success and (step_name in VALIDATE_AFTER) and VALIDATE_AFTER[step_name]['functions']:
            print(f"\n>  {B}Post-processing{X} ({Pipeline.step_name})")
            print("""  ─────────────────────────────────────────────────────────────────""")

            with padded_output(4):
                Pipeline.validate_steps         =   VALIDATE_AFTER
                Pipeline.run_validations()
            
        if Pipeline.validation_success:
            print(f"{B} finished : {c['bc']}{step_name}{X}")
        else:
            print(f"{B} skipped  : {c['bc']}{step_name}{X}")

@alfrd_cli.command()
def add(script_path: str, proj: str,
        symlink:    bool    = typer.Option(True, help="(instead of copying files a shortcut is placed in the project folder"), 
        ):
    """Add a new plugin to a specific project."""
    
    project_dir = proj_dir(proj)
    script_path = Path(script_path).absolute()

    if not script_path.is_file():
        print(f"File '{script_path}' not found.")
        raise typer.Exit()

    dest_path = project_dir / script_path.name
    # Path.rem(str(dest_path))
    if symlink:
        if Path(dest_path).exists():
            Path.unlink(dest_path)
        Path(dest_path).symlink_to(script_path)
    else:
        shutil.copy(script_path, dest_path)
    print(f"Added plugin to {proj}: {dest_path}")

@alfrd_cli.command()
def rm(proj: str):
    """Remove a specific project."""
    project_dir = proj_dir(proj)
    shutil.rmtree(project_dir)
    
    print(f"removed {proj}: {project_dir}")

if __name__ == "__main__":
    alfrd_cli()
