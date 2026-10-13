"""Reads class default properties from uncompressed UE3 packages (Section 8: Prejudice, version 678/5).

Unpack compressed packages (TGEngine.u, Engine.u) first with decompress.exe from S9Game/CookedPCFinal.
"""

import struct

PACKAGE_TAG = 0x9E2A83C1


class Package:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as f:
            self.data = d = f.read()
        tag, self.version, self.licensee = struct.unpack_from("<IHH", d, 0)
        if tag != PACKAGE_TAG:
            raise ValueError(f"{path}: not an Unreal package")
        o = 12
        (folder_len,) = struct.unpack_from("<i", d, o)
        o += 4 + (folder_len if folder_len > 0 else -folder_len * 2)
        o += 4  # package flags
        name_count, name_off, export_count, export_off, import_count, import_off = struct.unpack_from("<6i", d, o)
        self.names = self._read_names(name_off, name_count)
        self.imports = self._read_imports(import_off, import_count)
        self.exports = self._read_exports(export_off, export_count)

    def _read_names(self, o, count):
        names = []
        for _ in range(count):
            (n,) = struct.unpack_from("<i", self.data, o)
            o += 4
            if n >= 0:
                names.append(self.data[o : o + n - 1].decode("latin-1"))
                o += n
            else:
                names.append(self.data[o : o + (-n - 1) * 2].decode("utf-16le"))
                o += -n * 2
            o += 8  # object flags
        return names

    def _read_imports(self, o, count):
        imports = []
        for _ in range(count):
            _cp, _cpn, cn, _cnn, outer, on, _onn = struct.unpack_from("<7i", self.data, o)
            o += 28
            imports.append({"class": self.names[cn], "outer": outer, "name": self.names[on]})
        return imports

    def _read_exports(self, o, count):
        exports = []
        for _ in range(count):
            cls, sup, outer, on, onn, _arch = struct.unpack_from("<6i", self.data, o)
            o += 24 + 8  # + object flags
            size, off = struct.unpack_from("<ii", self.data, o)
            o += 8 + 4  # + export flags
            (net_count,) = struct.unpack_from("<i", self.data, o)
            o += 4 + 4 * net_count + 16 + 4  # net objects, package guid, package flags
            name = self.names[on] + (f"_{onn - 1}" if onn else "")
            exports.append({"class": cls, "super": sup, "outer": outer, "name": name, "size": size, "off": off})
        return exports

    def object_name(self, ref: int) -> str:
        if ref > 0:
            return self.exports[ref - 1]["name"]
        if ref < 0:
            return self.imports[-ref - 1]["name"]
        return "None"

    def class_name(self, export: dict) -> str:
        return self.object_name(export["class"]) if export["class"] else "Class"

    def find(self, name: str) -> list:
        return [e for e in self.exports if e["name"] == name]

    def _fname(self, o):
        i, n = struct.unpack_from("<ii", self.data, o)
        return self.names[i] + (f"_{n - 1}" if n else ""), o + 8

    def properties(self, o: int, end: int):
        """Returns ([(name, type, array_index, value)], the offset after the None terminator)."""
        out = []
        while o < end:
            name, o = self._fname(o)
            if name == "None":
                return out, o
            typ, o = self._fname(o)
            size, index = struct.unpack_from("<ii", self.data, o)
            o += 8
            extra = None
            if typ == "BoolProperty":
                out.append((name, typ, index, bool(self.data[o])))
                o += 1
                continue
            if typ in ("StructProperty", "ByteProperty"):
                extra, o = self._fname(o)
            out.append((name, typ, index, self._value(typ, extra, o, size)))
            o += size
        return out, o

    def _value(self, typ, extra, o, size):
        d = self.data
        if typ == "IntProperty":
            return struct.unpack_from("<i", d, o)[0]
        if typ == "FloatProperty":
            return struct.unpack_from("<f", d, o)[0]
        if typ in ("ObjectProperty", "ClassProperty", "ComponentProperty"):
            return self.object_name(struct.unpack_from("<i", d, o)[0])
        if typ == "NameProperty":
            return self._fname(o)[0]
        if typ == "ByteProperty":
            return self._fname(o)[0] if size == 8 else d[o]
        if typ == "StrProperty":
            (n,) = struct.unpack_from("<i", d, o)
            if n >= 0:
                return d[o + 4 : o + 4 + n - 1].decode("latin-1")
            return d[o + 4 : o + 4 + (-n - 1) * 2].decode("utf-16le")
        if typ == "StructProperty":
            try:
                return self.properties(o, o + size)[0]
            except Exception:
                return None
        if typ == "ArrayProperty":
            (count,) = struct.unpack_from("<i", d, o)
            if count == 0:
                return []
            if size - 4 == 4 * count:
                return list(struct.unpack_from(f"<{count}i", d, o + 4))
            items, p = [], o + 4
            try:
                for _ in range(count):
                    item, p = self.properties(p, o + size)
                    items.append(item)
            except Exception:
                return None
            return items
        return None

    def defaults(self, class_name: str) -> dict:
        export = self.find("Default__" + class_name)[0]
        props, _ = self.properties(export["off"] + 4, export["off"] + export["size"])
        return {name: value for name, _typ, index, value in props if index == 0}


def struct_rows(items) -> list:
    return [{name: value for name, _typ, _index, value in item} for item in items or []]
