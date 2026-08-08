[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/neka-nat-freecad-mcp-badge.png)](https://mseep.ai/app/neka-nat-freecad-mcp)

# FreeCAD MCP

This repository is a FreeCAD MCP that allows you to control FreeCAD from Claude Desktop.

## Demo

### Design a flange

![demo](./assets/freecad_mcp4.gif)

### Design a toy car

![demo](./assets/make_toycar4.gif)

### Design a part from 2D drawing

#### Input 2D drawing

![input](./assets/b9-1.png)

#### Demo

![demo](./assets/from_2ddrawing.gif)

This is the conversation history.
https://claude.ai/share/7b48fd60-68ba-46fb-bb21-2fbb17399b48

## Install addon

FreeCAD Addon directory is
* Windows: `%APPDATA%\FreeCAD\Mod\`
* Mac:
  * FreeCAD 1.1: `~/Library/Application\ Support/FreeCAD/v1-1/Mod/`
  * FreeCAD 1.0: `~/Library/Application\ Support/FreeCAD/v1-0/Mod/`
* Linux:
  * Ubuntu: `~/.FreeCAD/Mod/` or `~/snap/freecad/common/Mod/` (if you install FreeCAD from snap)
  * Debian: `~/.local/share/FreeCAD/Mod`
  * Arch / CachyOS (FreeCAD 1.1 from `extra/freecad`): `~/.local/share/FreeCAD/v1-1/Mod/`

Please put `addon/FreeCADMCP` directory to the addon directory.

```bash
git clone https://github.com/neka-nat/freecad-mcp.git
cd freecad-mcp

# For Linux (Ubuntu/Debian)
mkdir -p ~/.FreeCAD/Mod/
cp -r addon/FreeCADMCP ~/.FreeCAD/Mod/

# For Linux (Arch/CachyOS, FreeCAD 1.1 from extra/freecad)
mkdir -p ~/.local/share/FreeCAD/v1-1/Mod/
cp -r addon/FreeCADMCP ~/.local/share/FreeCAD/v1-1/Mod/

# For macOS (FreeCAD 1.1)
mkdir -p ~/Library/Application\ Support/FreeCAD/v1-1/Mod/
cp -r addon/FreeCADMCP ~/Library/Application\ Support/FreeCAD/v1-1/Mod/
```

When you install addon, you need to restart FreeCAD.
You can select "MCP Addon" from Workbench list and use it.

![workbench_list](./assets/workbench_list.png)

And you can start RPC server by "Start RPC Server" command in "FreeCAD MCP" toolbar.

![start_rpc_server](./assets/start_rpc_server.png)

### Auto-Start RPC Server

By default, the RPC server must be started manually each time FreeCAD opens. To start it automatically:

1. Open the **FreeCAD MCP** menu (switch to the MCP Addon workbench first)
2. Check **Auto-Start Server**

The setting is saved to `freecad_mcp_settings.json` and persists across sessions. On the next FreeCAD launch, the RPC server will start automatically once the application finishes loading.

You can disable it at any time by unchecking **Auto-Start Server** in the same menu.

## Setting up Claude Desktop

Pre-installation of the [uvx](https://docs.astral.sh/uv/guides/tools/) is required.

And you need to edit Claude Desktop config file, `claude_desktop_config.json`.

For user.

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uvx",
      "args": [
        "freecad-mcp"
      ]
    }
  }
}
```

If you want to save token, you can set `only_text_feedback` to `true` and use only text feedback.

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uvx",
      "args": [
        "freecad-mcp",
        "--only-text-feedback"
      ]
    }
  }
}
```

Screenshots can also be controlled per tool call instead of globally: every tool that returns a screenshot accepts an optional `include_screenshot` parameter (pass `false` to get text-only feedback, e.g. for analytical scripts or intermediate steps) and an optional `view_name` parameter to orient the screenshot ("Isometric" by default, or "Front", "Top", "Right", etc.). The `--only-text-feedback` flag always wins: when it is set, no screenshots are returned regardless of `include_screenshot`.


For developer.
First, you need clone this repository.

```bash
git clone https://github.com/neka-nat/freecad-mcp.git
```

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/freecad-mcp/",
        "run",
        "freecad-mcp"
      ]
    }
  }
}
```

## Remote Connections

By default the RPC server does not accept remote connections and listens on `localhost`. To control FreeCAD from another machine on your network:

### 1. Enable remote connections in FreeCAD

In the **FreeCAD MCP** toolbar:

1. Check **Remote Connections** — the RPC server will bind to `0.0.0.0` (all interfaces) on the next restart. For security reasons, it only accepts connections from the IP addresses or CIDR subnets specified in the **Allowed IPs** field. By default this is `127.0.0.1`.
2. Click **Configure Allowed IPs** and enter a comma-separated list of IP addresses or CIDR subnets that are allowed to connect, e.g.:

   ```
   192.168.1.100, 10.0.0.0/24
   ```

   `127.0.0.1` is always the default. Invalid entries are rejected with an error dialog. Restart the RPC server after changing these settings.

### 2. Point the MCP server at the remote host

Pass the `--host` flag with the IP address or hostname of the machine running FreeCAD:

```json
{
  "mcpServers": {
    "freecad": {
      "command": "uvx",
      "args": [
        "freecad-mcp",
        "--host", "192.168.1.100"
      ]
    }
  }
}
```

The `--host` value is validated on startup — it must be a valid IPv4/IPv6 address or hostname.

## Tools

All lengths are millimeters; angles are degrees. `create_document`/`create_object` report the name FreeCAD actually assigned (sanitized, deduplicated) — use that name in subsequent calls.

* `create_document`: Create a new document in FreeCAD.
* `create_object`: Create a new object in FreeCAD. Warns when the object does not recompute cleanly (e.g. a boolean missing Base/Tool).
* `edit_object`: Edit an object in FreeCAD.
* `delete_object`: Delete an object in FreeCAD.
* `execute_code`: Execute arbitrary Python code in FreeCAD. State persists across calls in a namespace isolated from the RPC server; failures return the traceback (with line numbers into your code) and any output printed before the error. Screenshots are off by default (`include_screenshot`); pass `dry_run=true` to run the code and then roll back all document changes.
* `execute_code_from_file`: Execute Python code from a file on the machine running the MCP server. Useful for long scripts that are iterated on: the agent writes the script to a file once, then edits it selectively and re-runs it without resending the full source each time. Also supports `dry_run` and `include_screenshot`.
* `execute_code_async`: Run background-safe code in a worker thread; returns a job id.
* `get_async_status`: Poll a background job (running/completed/failed with error + traceback).
* `insert_part_from_library`: Insert a part from the [parts library](https://github.com/FreeCAD/FreeCAD-library).
* `get_view`: Get a screenshot of the active view. Default screenshots are capped at 800 px on the longest edge; pass explicit `width`/`height` for full resolution.
* `get_views`: Get several views (default front/top/isometric) in one call, each image labelled — avoids repeated single-view screenshot cycles.
* `get_section_view`: Render a cutaway screenshot at a plane (XY/XZ/YZ) to see inside a model; the cut is rolled back so the document is never modified.
* `get_objects`: Get all objects in a document. `detail="summary"` (default) returns a compact health/envelope view and omits PartDesign Origin scaffolding; `detail="full"` returns every property.
* `get_object`: Get an object in a document (full detail, including `BoundBox`, `CenterOfMass`, validity, and recompute `State`).
* `measure_distance`: Minimum distance between two objects or sub-elements (`Face3`, `Edge1`, ...); reports intersection volume when shapes overlap.
* `get_topology`: Per-face type/area/centroid/normal breakdown (plus optional edges) — the face names FEM constraint `References` expect.
* `check_printability`: Basic 3D-print readiness — watertight/manifold check and an estimated minimum wall thickness; overhang analysis is opt-in.
* `save_document`: Save a document to `.FCStd` (documents live only in memory until saved).
* `export_document`: Export objects to STEP/IGES/BREP (exact) or STL/OBJ/3MF/PLY/AMF (mesh), chosen by file extension; `linear_deflection` controls mesh tessellation quality.
* `get_report_log`: Read FreeCAD's Report view console log (recompute errors, async job output, addon diagnostics).
* `get_parts_list`: Get the list of parts in the [parts library](https://github.com/FreeCAD/FreeCAD-library).
* `run_fem_analysis`: Run the CalculiX solver on an existing `Fem::FemAnalysis` and return summary results (max von Mises stress, max displacement, node count, working directory). Auto-creates a `SolverCcxTools` if the analysis has none. See [`examples/cantilever_fem.py`](examples/cantilever_fem.py) for an end-to-end usage example, and the `freecad://fem-workflow` MCP resource for setup recipes.

Tools that return a screenshot (`create_object`, `edit_object`, `delete_object`, `execute_code`, `insert_part_from_library`, `get_objects`, `get_object`, `run_fem_analysis`) accept optional `include_screenshot` (default `true`) and `view_name` (default `"Isometric"`) parameters to suppress or reorient the returned image per call.

## Contributors

<a href="https://github.com/neka-nat/freecad-mcp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=neka-nat/freecad-mcp" />
</a>

Made with [contrib.rocks](https://contrib.rocks).
