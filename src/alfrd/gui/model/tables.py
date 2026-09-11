from . import db
from sqlalchemy import Integer, String, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.mutable import MutableDict, MutableList

class ProjectDB(db.Model):
    __tablename__                                   =       "projects"
    id: Mapped[int]                                 =       mapped_column(Integer, primary_key=True)
    name: Mapped[str]                               =       mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None]                 =       mapped_column(String(255), nullable=True)
    configfile: Mapped[str | None]                  =       mapped_column(String(255), nullable=True)
    param: Mapped[dict | None]                      =       mapped_column(JSON, nullable=True)
    usesymlink : Mapped[bool]                       =       mapped_column(Boolean, default=True)
    registered_functions: Mapped[dict | None]       =       mapped_column(JSON, nullable=True)
    validator_functions: Mapped[dict | None]        =       mapped_column(JSON, nullable=True)
    validate_after: Mapped[dict | None]             =       mapped_column(JSON, nullable=True)
    validate_before: Mapped[dict | None]            =       mapped_column(JSON, nullable=True)
    
class WorkflowDB(db.Model):
    __tablename__                       =       "workflows"
    id: Mapped[int]                     =       mapped_column(Integer, primary_key=True)
    name: Mapped[str]                   =       mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None]     =       mapped_column(String(255), nullable=True)
    configfile: Mapped[str | None]      =       mapped_column(String(255), nullable=True)
    data: Mapped[dict | None]           =       mapped_column(JSON, nullable=True)
    sequence: Mapped[list | None]       =       mapped_column(MutableList.as_mutable(JSON), nullable=True)
    
class WorkflowSheet(db.Model):
    __tablename__                       =       "worksheets"
    id: Mapped[int]                     =       mapped_column(Integer, primary_key=True)
    name: Mapped[str]                   =       mapped_column(String(255), nullable=False, unique=True)
    
    

def create_project_row(proj, projconfig):
    """Creates a ProjectDB instance from project objects."""
    
    funcs                       =       projconfig.cleandata_foryaml(proj.get_functions())
    row = ProjectDB(
        name                    =       proj.name,
        description             =       proj.desc,
        configfile              =       str(projconfig.configfile) if projconfig.configfile and projconfig.configfile.exists() else None,
        param                   =       funcs,
        usesymlink              =       proj.use_symlink,
        registered_functions    =       funcs.get('REGISTERED'),
        validator_functions     =       funcs.get('VALIDATOR'),
        validate_after          =       funcs.get('VALIDATE_AFTER'),
        validate_before         =       funcs.get('VALIDATE_BEFORE')
    )
    
    return row