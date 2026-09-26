# Changelog

## [0.2.0.2]

### Fixed
- schema file pointed to the wrong location
- `alfrd.yaml` allows for similar name, i.e the database can have multiple manifest names and uses identifier for primary key using `hostname.absolute.path.with.dots.name-from-yaml
- improved results section
- forgotton project can be restored if the project is discoverable (e.g., not deleted/moved).

#### Added
- default values in `src/alfrd/web/assets/defaults/alfrd.yaml` and `src/alfrd/manifest_default.py`. Use `alfrd --project DIR` for default values.


[0.2.0.2]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.2
[0.2.0.1]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.1
[0.2.0.0]: https://github.com/avikhagol/alfrd/compare/v0.2.0...v0.2.0.0