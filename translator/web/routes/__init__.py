"""Register all route blueprints."""
from flask import Flask


def register_routes(app: Flask):
    from translator.web.routes.dashboard  import bp as bp_dash
    from translator.web.routes.mods       import bp as bp_mods
    from translator.web.routes.jobs       import bp as bp_jobs
    from translator.web.routes.backups    import bp as bp_backups
    from translator.web.routes.tools_rt   import bp as bp_tools
    from translator.web.routes.config_rt  import bp as bp_cfg
    from translator.web.routes.logs_rt    import bp as bp_logs
    from translator.web.routes.terms_rt   import bp as bp_terms
    from translator.web.routes.api        import bp as bp_api
    from translator.web.routes.servers_rt import bp as bp_servers

    app.register_blueprint(bp_dash)
    app.register_blueprint(bp_mods)
    app.register_blueprint(bp_jobs)
    app.register_blueprint(bp_backups)
    app.register_blueprint(bp_tools)
    app.register_blueprint(bp_cfg)
    app.register_blueprint(bp_logs)
    app.register_blueprint(bp_terms)
    app.register_blueprint(bp_api)
    app.register_blueprint(bp_servers)
