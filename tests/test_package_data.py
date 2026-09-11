from importlib import resources


EXPECTED_RESOURCES = (
    "templates/dashboard/index.htm",
    "templates/dashboard/layout.htm",
    "templates/dashboard/project_details.htm",
    "static/alfrd.css",
    "model/schema.sql",
)


def test_gui_resources_are_available_from_package():
    gui_root = resources.files("alfrd.gui")

    for relative_path in EXPECTED_RESOURCES:
        resource = gui_root.joinpath(*relative_path.split("/"))
        assert resource.is_file(), relative_path
        assert resource.read_bytes(), relative_path
