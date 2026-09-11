from alfrd.util import read_yaml
from pathlib import Path
import ast
import subprocess
import sys

def resolve_path_from_schema(schema: str, yaml_path: Path) -> Path:
    root = yaml_path.parent
    module_relpath = Path(*schema.split(".")).with_suffix(".py")
    matches = list(root.rglob(module_relpath.name))
    for match in matches:
        try:
            rel = match.relative_to(root)
        except ValueError:
            continue
        parts = rel.with_suffix("").parts
        if tuple(schema.split(".")) == parts[-len(schema.split(".")):]:
            return match
    raise FileNotFoundError(f"Could not resolve {schema}")

def class_info(node: ast.ClassDef) -> dict:
    info = {
        "help": ast.get_docstring(node) or "",
        "required_params": [],
        "default_values": {},
    }

    init = next(
        (
            n
            for n in node.body
            if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        ),
        None,
    )

    if init is None:
        return info

    args = init.args.args[1:]  # skip self
    defaults = init.args.defaults

    n_required = len(args) - len(defaults)

    for arg in args[:n_required]:
        info["required_params"].append(arg.arg)

    for arg, default in zip(args[n_required:], defaults):
        try:
            value = ast.literal_eval(default)
        except Exception:
            value = ast.unparse(default)

        info["default_values"][arg.arg] = value

    return info

class Inspector:
    def __init__(self, configfile:str =""):
        self.configfile     = configfile
        self.config         = read_yaml(Path(self.configfile))

    def print_config(self) -> None:
        for key, value in self.config.items():
            if isinstance(value, dict):
                print(f"{key}:")
                for subkey, subvalue in value.items():
                    print(f"  {subkey}: {subvalue}")
            elif isinstance(value, list):
                print(f"{key}:")
                for item in value:
                    print(f"  - {item}")
            else:
                print(f"{key}: {value}")

    def print_schema(self) -> None:
        _dic_res = {}
        if 'schema' not in self.config:
            return
        for schema in self.config['schema']:
            if 'definitions' not in schema:
                continue
            _dic_res[schema['name']] = {}
            schema_defs = schema['definitions']

            for schema_def in schema_defs:
                _schema_path = resolve_path_from_schema(schema_def, yaml_path=Path(self.configfile))
                path = Path(_schema_path)
                tree = ast.parse(path.read_text())

                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        _dic_res[schema['name']][node.name] = class_info(node)['help']

        for schema_name, schema_defs in _dic_res.items():
            print(f"Schema: {schema_name}")
            for class_name in schema_defs.keys():
                print(f"  {class_name}")

class Executor:
    def __init__(self, configfile: str):
            self.configfile = Path(configfile)
            self.config = read_yaml(self.configfile)

    def get_entrypoint(self, name: str) -> list[str]:
        for entry in self.config.get("entrypoint", []):
            if entry["name"] == name:
                return entry["cmd"]

        raise KeyError(f"No entrypoint named {name!r}")

    def run_entrypoint(self, name: str, extra_args: list[str] | None = None):
        cmd = self.get_entrypoint(name)

        if extra_args:
            cmd = cmd + extra_args

        # print("Running:", " ".join(cmd))
        result = subprocess.run(cmd)

        sys.exit(result.returncode)