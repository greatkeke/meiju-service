import app.api as api_mod
import app.main as main_mod

print("api app id:", id(api_mod.app), "routes:", len(api_mod.app.routes))
print("main app id:", id(main_mod.app))
for route in api_mod.app.routes:
    print(" ", type(route).__name__, repr(getattr(route, "path", None)), getattr(route, "methods", None))
print("router id:", id(api_mod.device_router), "routes:", len(api_mod.device_router.routes))
