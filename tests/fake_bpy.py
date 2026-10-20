"""A tiny stand-in for Blender's `bpy`, just enough to import the Turnpad
add-on (integrations/blender/turnpad) and run its runner, context tracker and
registration without Blender. `install()` puts it in sys.modules."""

import contextlib
import sys
import types


class Area:
    def __init__(self, type, width=100, height=100):
        self.type = type
        self.width = width
        self.height = height
        self.regions = [types.SimpleNamespace(type="HEADER"), types.SimpleNamespace(type="WINDOW")]


class Window:
    def __init__(self, *areas):
        self.screen = types.SimpleNamespace(areas=list(areas))
        self.workspace = types.SimpleNamespace(tools=types.SimpleNamespace(
            from_space_view3d_mode=lambda mode, create=False: types.SimpleNamespace(idname="builtin.move")))


class Ops:
    """bpy.ops: every operator is recorded as (path, execution context,
    properties); `results` and `errors` script the answers by operator path."""

    def __init__(self):
        self.calls = []
        self.results = {}
        self.errors = {}
        self.overrides_seen = []

    def __getattr__(self, category):
        ops = self

        class Category:
            def __getattr__(self, name):
                path = "%s.%s" % (category, name)

                def call(exec_context, /, **props):
                    ops.calls.append((path, exec_context, props))
                    ops.overrides_seen.append(dict(FakeBpy.current_override))
                    if path in ops.errors:
                        raise ops.errors[path]
                    return ops.results.get(path, {"FINISHED"})
                return call
        return Category()


class Timers:
    def __init__(self):
        self.functions = {}

    def register(self, function, first_interval=0.0, persistent=False):
        self.functions[function] = first_interval

    def unregister(self, function):
        del self.functions[function]

    def is_registered(self, function):
        return function in self.functions


class AddonPreferences:
    pass


class FakeBpy(types.ModuleType):
    current_override = {}

    def __init__(self):
        super().__init__("bpy")
        self.ops = Ops()
        self.app = types.ModuleType("bpy.app")
        self.app.background = False
        self.app.version_string = "5.2.2 LTS"
        self.app.timers = Timers()
        self.app.handlers = types.ModuleType("bpy.app.handlers")
        self.app.handlers.load_post = []
        self.app.handlers.persistent = lambda function: function
        self.props = types.ModuleType("bpy.props")
        self.props.BoolProperty = lambda **kw: ("bool", kw)
        self.props.StringProperty = lambda **kw: ("string", kw)
        self.types = types.SimpleNamespace(AddonPreferences=AddonPreferences)
        self.registered = []
        self.utils = types.SimpleNamespace(
            register_class=self.registered.append,
            unregister_class=self.registered.remove)
        self.windows = []
        self.context = types.SimpleNamespace(
            mode="OBJECT",
            window_manager=types.SimpleNamespace(windows=self.windows),
            workspace=None,
            tool_settings=types.SimpleNamespace(mesh_select_mode=(True, False, False)),
            temp_override=self._temp_override)

    @contextlib.contextmanager
    def _temp_override(self, **kwargs):
        previous = FakeBpy.current_override
        FakeBpy.current_override = kwargs
        try:
            yield
        finally:
            FakeBpy.current_override = previous


def install():
    """Install a fresh fake and return it (replaces an earlier one)."""
    fake = FakeBpy()
    sys.modules["bpy"] = fake
    sys.modules["bpy.app"] = fake.app
    sys.modules["bpy.app.handlers"] = fake.app.handlers
    sys.modules["bpy.props"] = fake.props
    return fake
