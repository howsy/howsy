#!/usr/bin/env python3
"""Bundles noVNC's npm build (CommonJS, lib/*.js) into one browser script that
defines window.NoVNC_RFB. The console's remote-control page uses it; bundling
keeps the console working without internet access or a JS build toolchain.

  curl -fLO https://registry.npmjs.org/@novnc/novnc/-/novnc-1.5.0.tgz
  tar xzf novnc-1.5.0.tgz
  python3 tools/build-novnc.py package services/console-ui/vendor/novnc
"""
import json
import os
import shutil
import sys

src, out = sys.argv[1], sys.argv[2]
lib = os.path.join(src, "lib")
version = json.load(open(os.path.join(src, "package.json")))["version"]
modules = []
for root, _, files in os.walk(lib):
    for f in sorted(files):
        if f.endswith(".js"):
            path = os.path.relpath(os.path.join(root, f), lib).replace(os.sep, "/")
            code = open(os.path.join(root, f), encoding="utf-8").read()
            modules.append(f"{json.dumps(path)}: function (require, module, exports) {{\n{code}\n}}")

bundle = f"""/* noVNC {version} (MPL-2.0, https://github.com/novnc/noVNC), bundled by tools/build-novnc.py */
(function () {{
  "use strict";
  var defs = {{
{",\n".join(modules)}
  }};
  var cache = {{}};
  function resolve(from, req) {{
    var parts = from.split("/"); parts.pop();
    req.split("/").forEach(function (p) {{
      if (p === "..") parts.pop(); else if (p !== ".") parts.push(p);
    }});
    var path = parts.join("/");
    if (!defs[path] && defs[path + ".js"]) path += ".js";
    if (!defs[path] && defs[path + "/index.js"]) path += "/index.js";
    return path;
  }}
  function load(path) {{
    if (cache[path]) return cache[path].exports;
    if (!defs[path]) throw new Error("noVNC bundle: missing module " + path);
    var module = cache[path] = {{ exports: {{}} }};
    defs[path](function (req) {{ return load(resolve(path, req)); }}, module, module.exports);
    return module.exports;
  }}
  window.NoVNC_RFB = load("rfb.js")["default"];
}})();
"""
os.makedirs(out, exist_ok=True)
open(os.path.join(out, "novnc.js"), "w", encoding="utf-8").write(bundle)
shutil.copy(os.path.join(src, "LICENSE.txt"), os.path.join(out, "LICENSE.txt"))
print(f"noVNC {version}: {len(modules)} modules, {len(bundle) // 1024} KB")
