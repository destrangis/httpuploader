import argparse
import io
import json
import mimetypes
import os
import pathlib
import sys
import tarfile
import traceback
import zipfile
from wsgiref.util import FileWrapper
from urllib.parse import parse_qs
from tempfile import TemporaryFile


class HTUPLError(Exception):
    def __init__(self, errno, msg, extra):
        self.errno, self.msg, self.extra = errno, msg, extra


def gather_api_versions(cls):
    versionlst = []
    for klass in cls.__subclasses__():
        versionlst += gather_api_versions(klass)
        major, minor, patch = klass.version.split(".")
        version = [int(major), int(minor), int(patch)]
        versionlst.append((version, klass))
    return versionlst


class API:
    versionlst = []

    @classmethod
    def get_versions(cls):
        versionlst = gather_api_versions(cls)
        versionlst.sort(reverse=True)
        cls.versionlst = versionlst

    @classmethod
    def find_version(cls, verstring):
        if not cls.versionlst:
            cls.get_versions()
        selection = cls.versionlst
        vers_components = [int(c) for c in verstring.split(".")]
        for i, comp in enumerate(vers_components):
            lastsel = selection
            selection = [x for x in selection if x[0][i] == comp]
            if len(selection) == 0:
                selection = lastsel
                break
        if len(selection) > 0:
            return selection[0]

        return cls.versionlst[0]

    @classmethod
    def latest_version(cls):
        if not cls.versionlst:
            cls.get_versions()
        last = cls.versionlst[0]
        return ".".join([str(x) for x in last[0]])

    def __init__(self, *args):
        raise NotImplementedError(
            "Class '{}' Cannot be instantiated directly.".format(
                self.__class__.__name__
            )
        )

    def response_json(self, rc, msg, data=None):
        return {
            "rc": rc,
            "msg": msg,
            "version": self.version,
            "data": data if data is not None else {},
        }


class APIv1(API):
    version = "1.0.0"

    def __init__(self, topdir, hidden_files=False):
        self.topdir = topdir
        self.hidden_files = hidden_files

        self.headers = []
        self.response = "204 No content"
        self.result = []

        self.dirops = {
            ("GET", ""): self.dir_list,
            ("GET", "list"): self.dir_list,
            ("GET", "archive"): self.dir_archive,
            ("POST", "mkdir"): self.mkdir,
            ("POST", "upload"): self.upload,
            ("DELETE", ""): self.deldir,
        }
        self.fileops = {
            ("GET", ""): self.download_file,
            ("GET", "download"): self.download_file,
            ("GET", "compress"): self.compress_file,
            ("GET", "info"): self.file_info,
            ("GET", "checksum"): self.checksum,
            ("POST", "copy"): self.copy,
            ("POST", "move"): self.move,
            ("DELETE", ""): self.delfile,
        }

    def run(self, callmap, method):
        cmd = callmap["cmd"]
        path = callmap["path"]
        args = callmap["args"]
        if path.is_dir():
            ops = self.dirops
        elif path.is_file():
            ops = self.fileops
        else:
            raise HTUPLError(
                400,
                "Bad request",
                "{} {} not a directory nor a file.".format(method, path),
            )

        action = ops.get((method, cmd))
        if action:
            action(path, args)
        else:
            self.badop(path, args, method)

    def badop(self, path, args, method):
        raise HTUPLError(
            400, "Bad request", "{} {} cannot process.".format(method, path)
        )

    def dir_list(self, path, args):
        def dir_node(name, hrefpath):
            node = {
                "name": name,
                "links": {
                    "list": {
                        "method": "GET",
                        "href": hrefpath + "?cmd=list",
                        "args": [],
                    },
                    "archive": {
                        "method": "GET",
                        "href": hrefpath + "?cmd=archive&format={0}",
                        "args": ["format"],
                    },
                    "mkdir": {
                        "method": "POST",
                        "href": hrefpath + "?cmd=mkdir&dir={0}",
                        "args": ["dir"],
                    },
                    "upload": {
                        "method": "POST",
                        "href": hrefpath + "?cmd=upload",
                        "args": [],
                    },
                    "delete": {"method": "DELETE", "href": hrefpath, "args": []},
                },
            }
            return node

        def file_node(name, size, hrefpath):
            node = {
                "name": name,
                "size": human_size(size),
                "links": {
                    "info": {
                        "method": "GET",
                        "href": hrefpath + "?cmd=info",
                        "args": [],
                    },
                    "compress": {
                        "method": "GET",
                        "href": hrefpath + "?cmd=compress&format={0}",
                        "args": ["format"],
                    },
                    "checksum": {
                        "method": "GET",
                        "href": hrefpath + "?cmd=checksum",
                        "args": [],
                    },
                    "match_checksum": {
                        "method": "GET",
                        "href": hrefpath + "?cmd=checksum&check={0}",
                        "args": ["check"],
                    },
                    "download": {
                        "method": "GET",
                        "href": hrefpath + "?cmd=download",
                        "args": [],
                    },
                    "copy": {
                        "method": "POST",
                        "href": hrefpath + "?cmd=copy&dest={0}",
                        "args": ["dest"],
                    },
                    "move": {
                        "method": "POST",
                        "href": hrefpath + "?cmd=move&dest={0}",
                        "args": ["dest"],
                    },
                    "delete": {"method": "DELETE", "href": hrefpath, "args": []},
                },
            }
            return node

        rtarget = path.resolve().relative_to(self.topdir)
        rspdict = self.response_json(200, "OK")
        listdirs = []
        listfiles = []
        for item in path.iterdir():
            if is_hidden(item) and not self.hidden_files:
                continue

            relitem = item.resolve().relative_to(self.topdir)
            href = "/api/" + self.version + "/" + str(relitem)
            if item.is_file():
                size = item.stat().st_size
                listfiles.append(file_node(item.name, size, href))
            if item.is_dir():
                listdirs.append(dir_node(item.name, href))

        listfiles.sort(key=lambda x: x["name"])
        listdirs.sort(key=lambda x: x["name"])
        apiparent = "/api/" + self.version + "/" + str(rtarget.parent)
        listdirs = [
            {
                "name": "..",
                "links": {
                    "list": {
                        "method": "GET",
                        "href": apiparent + "?cmd=list",
                        "args": [],
                    }
                },
            }
        ] + listdirs

        name = "/" if str(rtarget) == "." else "/" + str(rtarget)
        data = dir_node(name, "/api/" + self.version + name)
        data["files"] = listfiles
        data["directories"] = listdirs
        rspdict["data"] = data

        self.headers = [("Content-type", "application/json")]
        self.response = "200 OK"
        self.result = [json.dumps(rspdict, indent=2).encode()]

    def dir_archive(self, path, args):
        fmt = args.get("format", ["zip"])[0]
        if fmt == "zip":
            self.zip_archive(path)
        elif fmt == "tar.gz":
            self.tar_archive(path)
        else:
            resp = self.response_json(
                400,
                "Bad request",
                {"extra": "Directory archive. Bad format {}".format(fmt)},
            )
            self.headers = [("Content-type", "application/json")]
            self.response = "400 Bad request"
            self.result = [json.dumps(resp, indent=2).encode()]

    def zip_archive(self, path):
        tfd = TemporaryFile()
        with zipfile.ZipFile(tfd, "w", zipfile.ZIP_DEFLATED) as zp:
            for dp, dirs, filenames in os.walk(path):
                for filename in filenames:
                    fullfile = pathlib.Path(dp) / filename
                    relfile = fullfile.relative_to(path)
                    zp.write(str(fullfile.resolve()), str(relfile))
        tfd.seek(0, io.SEEK_END)
        size = tfd.tell()
        tfd.seek(0)
        arch_name = path.name if path.name else "top"
        resp = self.response_json(200, "OK")
        self.response = "200 OK"
        self.headers = [
            ("Content-length", str(size)),
            ("Content-type", "  application/zip"),
            ("Content-disposition", "attachment; filename=" + arch_name + ".zip"),
        ]
        self.result = FileWrapper(tfd)

    def tar_archive(self, path):
        fullpath = path.resolve()
        tfd = TemporaryFile()
        tarfd = tarfile.open(fileobj=tfd, mode="x:gz")
        with os.scandir(fullpath) as scd:
            for entry in scd:
                fullname = fullpath / entry.name
                relname = fullname.relative_to(fullpath)
                tarfd.add(str(fullname), str(relname))
        tarfd.close()

        tfd.seek(0, io.SEEK_END)
        size = tfd.tell()
        tfd.seek(0)
        arch_name = path.name if path.name else "top"
        resp = self.response_json(200, "OK")
        self.response = "200 OK"
        self.headers = [
            ("Content-length", str(size)),
            ("Content-type", "application/gzip"),
            ("Content-disposition", "attachment; filename=" + arch_name + ".tar.gz"),
        ]
        self.result = FileWrapper(tfd)

    def mkdir(self, path, args):
        pass

    def deldir(self, path, args):
        pass

    def download_file(self, pfile, args):
        stinfo = pfile.stat()
        mime, enc = mimetypes.guess_type(str(pfile))
        if not mime:
            mime = "application/octet-stream"

        self.headers = [
            ("Content-length", str(stinfo.st_size)),
            ("Content-type", mime),
            ("Content-disposition", "attachment; filename=" + pfile.name),
        ]
        if enc:
            self.headers.append(("Content-encoding", enc))

        self.response = "200 OK"
        self.result = FileWrapper(pfile.open("rb"))

    def compress_file(self, path, args):
        pass

    def file_info(self, path, args):
        pass

    def checksum(self, path, args):
        pass

    def upload(self, path, args):
        pass

    def copy(self, path, args):
        pass

    def move(self, path, args):
        pass

    def delfile(self, path, args):
        pass


def is_hidden(path):
    st = path.stat()
    if getattr(st, "st_file_attributes", None):
        return (st.st_file_attributes | stat.stat.FILE_ATTRIBUTE_HIDDEN) != 0
    return path.name[0] == "."


def human_size(size):
    units = ["KB", "MB", "GB", "TB"]
    n = size
    lastu = "bytes"
    for u in units:
        lastn = n
        n = n / 1024
        if n < 1:
            return "{0:.2f} {1}".format(lastn, lastu)
        lastu = u
    else:
        return "{0:.2f} {1}".format(n, lastu)


class WSGIApp:
    def __init__(self, topdir=".", hidden_files=False):
        self.topdir = pathlib.Path(topdir).resolve()
        self.hidden_files = hidden_files

    def serve_jsapp(self, startdir):
        html = """<?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE html
             PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN"
            "http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">
        <html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
          <head>
            <title>Hello World</title>
          </head>
          <body>
            <h3>Hello World</h3>
            <p>{}</p>
          </body>
        </html>""".format(
            startdir
        )
        self.start_response("200 OK", [("Content-type", "text/html")])
        return [html.encode()]

    def error(self, errno, msg, extra, version=""):
        self.start_response(
            "{} {}".format(errno, msg), [("Content-type", "application/json")]
        )
        return [
            json.dumps(
                {
                    "version": API.latest_version() if not version else version,
                    "rc": errno,
                    "msg": msg,
                    "data": {"extra": extra},
                }
            ).encode()
        ]

    def send_favicon(self):
        self.start_response("204 No content", [])
        return []

    def check_valid(self, strpath):
        p = self.topdir / strpath
        try:
            relpath = p.relative_to(self.topdir)
        except ValueError:
            return False
        return relpath is not None

    def dispatch_api_call(self, apidict, method):
        version_tpl, APIClass = API.find_version(apidict["version"])
        api = APIClass(self.topdir, self.hidden_files)
        api.run(apidict, method)

        self.start_response(api.response, api.headers)
        return api.result

    def parse_request(self, rqst, qstr):
        qdict = parse_qs(qstr)
        strippedpath = rqst.strip("/")

        if not self.check_valid(strippedpath):
            raise HTUPLError(403, "Forbidden", "Path {} not accessible.".format(rqst))

        version = ""
        if rqst.startswith("/api/"):
            parts = strippedpath.split("/", 2)
            version = parts[1]
            objloc = parts[2] if len(parts) > 2 else "."
        elif not qdict:
            objloc = strippedpath
        else:
            raise HTUPLError(
                400, "Bad request", "Bad URL (query string only allowed on API calls)"
            )

        pathname = self.topdir / objloc
        return version, pathname, qdict

    def __call__(self, env, start_response):
        self.env = env
        self.start_response = start_response
        method = env["REQUEST_METHOD"]

        request = env.get("PATH_INFO", "")
        querystring = env.get("QUERY_STRING", "")

        if request == "/favicon.ico":
            return self.send_favicon()

        try:
            apiversion, resource, querydict = self.parse_request(request, querystring)
        except HTUPLError as err:
            return self.error(err.errno, err.msg, err.extra)

        if apiversion:
            cmd = querydict.pop("cmd", [""])[0].lower()
            apidict = {
                "version": apiversion,
                "path": resource,
                "cmd": cmd,
                "args": querydict,
            }
        elif method == "GET":
            if resource.is_dir():
                return self.serve_jsapp(resource)
            apidict = {
                "version": API.latest_version(),
                "path": resource,
                "cmd": "download",
                "args": {},
            }
        elif method == "POST":
            apidict = {
                "version": API.latest_version(),
                "path": resource,
                "cmd": "upload",
                "args": {},
            }
        try:
            return self.dispatch_api_call(apidict, method)
        except HTUPLError as err:
            return self.error(err.errno, err.msg, err.extra)
        except Exception:
            tp, val, tb = sys.exc_info()
            return self.error(
                500, "Server error", "".join(traceback.format_exception(tp, val, tb))
            )


def get_cli_arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--rootdir",
        "-d",
        metavar="DIR",
        default=".",
        type=pathlib.Path,
        help="set the root of the directory hierarchy to DIR",
    )
    parser.add_argument(
        "--port",
        "-p",
        metavar="PORT",
        default=8018,
        type=int,
        help="listen for connections on specified PORT",
    )
    parser.add_argument(
        "--show-hidden",
        "-s",
        default=False,
        action="store_true",
        help="reveal hidden files and directories",
    )

    return parser.parse_args(argv)


def main(argv=None):
    from wsgiref.simple_server import make_server, WSGIServer
    from socketserver import ThreadingMixIn

    class MTServer(ThreadingMixIn, WSGIServer):
        pass

    if argv is None:
        argv = sys.argv[1:]

    args = get_cli_arguments(argv)
    port = args.port

    ul_serve = WSGIApp(topdir=args.rootdir, hidden_files=args.show_hidden)
    srv = make_server("", port, ul_serve, server_class=MTServer)
    print("Listening on port {0}".format(port), file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
