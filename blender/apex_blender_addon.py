"""Apex Bridge — lets Apex ask Blender to create simple measured objects.

Install: Blender > Edit > Preferences > Add-ons > Install... > pick this file
> enable "Apex Bridge". Then open the N-panel in the 3D viewport (press N),
find the "Apex" tab, and click Start Server. Leave Blender open — this add-on
IS the server Apex's agent/blender_bridge.py connects to.

## Why this exists, and why it refuses more than it allows

Apex cannot run Blender's Python API (`bpy`) itself — it is only safe to call
from Blender's own main thread, inside Blender's own process. So this add-on
is the other half of one feature split across two programs: Apex validates and
asks; this executes and reports back. Nothing here executes arbitrary code
sent over the socket. The command set is fixed on purpose — `ping`,
`create_primitive`, `set_color`, `export_glb` — because the feature this add-on
was built for explicitly EXCLUDES "unrestricted natural-language Python
execution inside Blender" from its scope. Apex enforces the same shape
allowlist and dimension bounds independently on its own side
(`agent/blender_bridge.py`); this add-on does not trust that and checks again,
because either side being wired up wrong should not remove the other's guard.

## The threading constraint this whole file exists around

`bpy` calls are only safe on Blender's main thread. The socket server runs on a
background thread (so accepting a connection never blocks Blender's UI), but it
never touches `bpy` directly — it drops each parsed command into a queue and a
`bpy.app.timers` callback (which Blender always runs on the main thread) drains
that queue and does the actual scene work, then wakes the waiting connection
thread with the result. Skipping this and calling `bpy` from the socket thread
would crash Blender intermittently, in a way that looks like Blender itself is
unstable rather than like a threading bug in this add-on.
"""
bl_info = {
    "name": "Apex Bridge",
    "author": "Apex",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Apex",
    "description": "Lets Apex create simple measured objects by voice, over a local socket",
    "category": "Interface",
}

import json
import os
import queue
import socket
import socketserver
import threading
import time

import bpy  # noqa: E402  (Blender injects this; unavailable outside Blender)

# Every export goes here, and NOWHERE ELSE — Apex sends only a filename, never
# a path, and this is the one place that decides where that filename lands.
# Trusting a path from the socket would make this add-on a way to write
# anywhere on disk that Blender's process can reach.
EXPORT_ROOT = os.path.expanduser("~/.apex/blender_exports")

SHAPES = {"cube", "sphere", "cylinder", "cone", "plane", "torus"}
_REQUIRED_DIMS = {
    "cube": ("width", "depth", "height"),
    "plane": ("width", "depth"),
    "sphere": ("diameter",),
    "cylinder": ("diameter", "height"),
    "cone": ("diameter", "height"),
    "torus": ("diameter", "tube_diameter"),
}

_job_queue: "queue.Queue" = queue.Queue()
_server = None
_server_thread = None


def _safe_export_path(filename: str) -> str:
    """Confine `filename` to EXPORT_ROOT the same way agent/props.py confines a
    prop path — resolve, then check containment, never trust the string."""
    name = os.path.basename(str(filename or "").strip())
    if not name or not name.lower().endswith(".glb"):
        raise ValueError("filename must be a bare '*.glb' name, no path")
    os.makedirs(EXPORT_ROOT, exist_ok=True)
    target = os.path.realpath(os.path.join(EXPORT_ROOT, name))
    root = os.path.realpath(EXPORT_ROOT)
    if os.path.commonpath([root, target]) != root:
        raise ValueError("refused: that filename would leave the export folder")
    return target


def _do_create_primitive(cmd: dict) -> dict:
    shape = str(cmd.get("shape", "")).strip().lower()
    if shape not in SHAPES:
        return {"ok": False, "error": f"unknown shape '{shape}'"}
    dims_mm = cmd.get("dims_mm") or {}
    needed = _REQUIRED_DIMS[shape]
    dims_m = {}
    for key in needed:
        if key not in dims_mm:
            return {"ok": False, "error": f"missing dimension '{key}' for {shape}"}
        try:
            dims_m[key] = float(dims_mm[key]) / 1000.0   # mm -> Blender metres
        except (TypeError, ValueError):
            return {"ok": False, "error": f"bad dimension '{key}': {dims_mm[key]!r}"}
        if dims_m[key] <= 0:
            return {"ok": False, "error": f"'{key}' must be positive"}

    ops = bpy.ops.mesh
    if shape == "cube":
        ops.primitive_cube_add()
    elif shape == "plane":
        ops.primitive_plane_add()
    elif shape == "sphere":
        ops.primitive_uv_sphere_add()
    elif shape == "cylinder":
        ops.primitive_cylinder_add()
    elif shape == "cone":
        ops.primitive_cone_add()
    elif shape == "torus":
        ops.primitive_torus_add()

    obj = bpy.context.active_object
    if obj is None:
        return {"ok": False, "error": "Blender did not create an object"}

    # Setting .dimensions directly (rather than hand-computing a scale factor
    # per primitive type) lets Blender do that math — it already knows each
    # primitive's default bounding box.
    if shape in ("cube", "plane"):
        obj.dimensions = (dims_m["width"], dims_m["depth"],
                          dims_m.get("height", obj.dimensions[2]) if shape == "cube" else obj.dimensions[2])
    elif shape == "sphere":
        d = dims_m["diameter"]
        obj.dimensions = (d, d, d)
    elif shape in ("cylinder", "cone"):
        d = dims_m["diameter"]
        obj.dimensions = (d, d, dims_m["height"])
    elif shape == "torus":
        d = dims_m["diameter"]
        obj.dimensions = (d, d, dims_m["tube_diameter"])

    requested = str(cmd.get("name") or shape)
    obj.name = requested
    # Blender de-duplicates a taken name as "name.001" itself — report back
    # whatever it actually settled on, so Apex references the real object.
    final_name = obj.name

    color = cmd.get("color")
    if color and len(color) >= 3:
        _apply_color(obj, color)

    return {"ok": True, "name": final_name}


def _apply_color(obj, rgba) -> None:
    mat = bpy.data.materials.new(name=f"{obj.name}_mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    rgba = list(rgba) + [1.0] * (4 - len(rgba))
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = tuple(rgba[:4])
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def _do_set_color(cmd: dict) -> dict:
    name = str(cmd.get("name", ""))
    obj = bpy.data.objects.get(name)
    if obj is None:
        return {"ok": False, "error": f"no object named '{name}'"}
    color = cmd.get("color")
    if not color or len(color) < 3:
        return {"ok": False, "error": "color must be [r,g,b] or [r,g,b,a] in 0..1"}
    _apply_color(obj, color)
    return {"ok": True, "name": name}


def _do_export_glb(cmd: dict) -> dict:
    name = str(cmd.get("name", ""))
    obj = bpy.data.objects.get(name)
    if obj is None:
        return {"ok": False, "error": f"no object named '{name}'"}
    try:
        path = _safe_export_path(cmd.get("filename", ""))
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB",
                              use_selection=True)
    return {"ok": True, "export_dir": EXPORT_ROOT, "path": path}


_DISPATCH = {
    "ping": lambda cmd: {"ok": True, "blender_version": bpy.app.version_string},
    "create_primitive": _do_create_primitive,
    "set_color": _do_set_color,
    "export_glb": _do_export_glb,
}


def _drain_queue():
    """Runs on Blender's main thread via bpy.app.timers. Reschedules itself —
    the return value is the delay (seconds) until Blender calls it again."""
    try:
        while True:
            cmd, result_box, done = _job_queue.get_nowait()
            try:
                name = cmd.get("cmd")
                handler = _DISPATCH.get(name)
                result_box["result"] = (
                    handler(cmd) if handler else
                    {"ok": False, "error": f"unknown command '{name}'"})
            except Exception as e:
                result_box["result"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            finally:
                done.set()
    except queue.Empty:
        pass
    return 0.05


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(30)
        buf = b""
        while b"\n" not in buf:
            chunk = self.request.recv(65536)
            if not chunk:
                return
            buf += chunk
        try:
            cmd = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except Exception:
            self.request.sendall(b'{"ok": false, "error": "bad JSON"}\n')
            return

        result_box, done = {}, threading.Event()
        _job_queue.put((cmd, result_box, done))
        if not done.wait(25):
            self.request.sendall(b'{"ok": false, "error": "timed out waiting for Blender main thread"}\n')
            return
        self.request.sendall((json.dumps(result_box["result"]) + "\n").encode("utf-8"))


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server(host="127.0.0.1", port=8799) -> str:
    global _server, _server_thread
    if _server is not None:
        return "already running"
    _server = _Server((host, port), _Handler)
    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    if not bpy.app.timers.is_registered(_drain_queue):
        bpy.app.timers.register(_drain_queue)
    return f"listening on {host}:{port}"


def stop_server() -> str:
    global _server, _server_thread
    if _server is None:
        return "not running"
    _server.shutdown()
    _server.server_close()
    _server = None
    _server_thread = None
    return "stopped"


class APEX_OT_start_server(bpy.types.Operator):
    bl_idname = "apex.start_server"
    bl_label = "Start Apex Bridge Server"

    def execute(self, context):
        msg = start_server(port=context.scene.apex_bridge_port)
        self.report({"INFO"}, f"Apex Bridge: {msg}")
        return {"FINISHED"}


class APEX_OT_stop_server(bpy.types.Operator):
    bl_idname = "apex.stop_server"
    bl_label = "Stop Apex Bridge Server"

    def execute(self, context):
        msg = stop_server()
        self.report({"INFO"}, f"Apex Bridge: {msg}")
        return {"FINISHED"}


class APEX_PT_panel(bpy.types.Panel):
    bl_label = "Apex"
    bl_idname = "APEX_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Apex"

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "apex_bridge_port")
        layout.label(text=f"Exports: {EXPORT_ROOT}")
        running = _server is not None
        layout.label(text="Status: " + ("running" if running else "stopped"))
        if running:
            layout.operator("apex.stop_server")
        else:
            layout.operator("apex.start_server")


_CLASSES = (APEX_OT_start_server, APEX_OT_stop_server, APEX_PT_panel)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.apex_bridge_port = bpy.props.IntProperty(
        name="Port", default=8799, min=1024, max=65535)


def unregister():
    stop_server()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.apex_bridge_port


if __name__ == "__main__":
    register()
