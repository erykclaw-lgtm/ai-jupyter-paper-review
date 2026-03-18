from .handlers import setup_handlers


def _jupyter_labextension_paths():
    return [{"src": "labextension", "dest": "@paper-review/labextension"}]


def _jupyter_server_extension_points():
    return [{"module": "jupyter_paper_review"}]


def _load_jupyter_server_extension(server_app):
    """Register the API handlers."""
    setup_handlers(server_app.web_app)
    server_app.log.info("jupyter_paper_review server extension loaded.")


def _unload_jupyter_server_extension(server_app):
    """Clean up SDK clients on server shutdown."""
    from .handlers import _bridge

    if _bridge:
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_bridge.shutdown())
            else:
                loop.run_until_complete(_bridge.shutdown())
        except Exception:
            pass
