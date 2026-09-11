from flask import Blueprint, render_template, abort
from alfrd.core.project import Project
from alfrd.gui.model.tables import ProjectDB
from jinja2 import TemplateNotFound

api = Blueprint('api', __name__, url_prefix='/api')
dashboard = Blueprint('dashboard', __name__, url_prefix='/dashboard', template_folder='templates')

@api.route('/', methods=['GET', 'POST'])
def index():
    return {"hello":"world"}

simple_page = Blueprint('simple_page', __name__,
                        template_folder='templates')

@api.route('/project/<proj_name>', methods=['GET'])
def project_api(proj_name):
    proj = Project(proj_name)
    # proj.configure().load()
    proj.clear_project()
    proj.load_project()
    
    project = ProjectDB.query.filter_by(name=proj_name).first()
    if not proj.get_projdir().exists():
        abort(404, description=f"Project '{proj_name}' not found.")
    elif not project:
        abort(422, description=f"Project '{proj_name}' not registered!.")
        
        
    return {
        "name": project.name,
        "description": project.description,
        "configfile": project.configfile, #if projconfig.configfile and projconfig.configfile.exists() else None,
        "param": project.param,
        "usesymlink": project.usesymlink,
        "registered_functions": project.registered_functions,
        "validator_functions": project.validator_functions,
        "validate_after": project.validate_after,
        "validate_before": project.validate_before,
    }
    
    
@dashboard.route('/', methods=['GET', 'POST'])
def index_dashboard():
    all_projects = ProjectDB.query.all()
    return render_template('dashboard/index.htm', title='Project', projects=all_projects)

@dashboard.route('/project/<proj_name>', methods=['GET', 'POST'])
def project_details(proj_name):
    proj         =  project_api(proj_name=proj_name)    
    return render_template('dashboard/project_details.htm', project=proj)
    