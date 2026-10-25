from app.api import app
from app.device_api import router

print("router routes:", [(sorted(r.methods)[0], r.path) for r in router.routes])
print("app total:", len(app.routes))
for route in app.routes:
    path = getattr(route, "path", "?")
    methods = sorted(getattr(route, "methods", []) or [])
    print(" ", methods, path)
