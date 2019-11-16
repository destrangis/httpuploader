import argparse
import base64
import gzip
import hashlib
import io
import json
import mimetypes
import os
import pathlib
import shutil
import sys
import tarfile
import traceback
import zipfile
from wsgiref.util import FileWrapper
from urllib.parse import parse_qs
from tempfile import TemporaryFile
from cgi import FieldStorage
from pprint import pformat
from datetime import datetime, timedelta, timezone

CHUNKSIZE = 65536  # 64KB

MODULE_VERSION = "1.0.0"

LOCAL_TZ = datetime.now(timezone(timedelta(0))).astimezone().tzinfo


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
            "api_version": self.version,
            "data": data if data is not None else {},
        }


class APIv1(API):
    version = "1.0.0"

    def __init__(self, env, topdir, hidden_files=False):
        self.env = env
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
        if path.is_dir() or cmd == "mkdir" or cmd == "upload":
            ops = self.dirops
        elif path.is_file():
            ops = self.fileops
        else:
            raise HTUPLError(
                404,
                "Not found",
                "'{}' not a directory nor a file.\nMethod: '{}', Cmd: '{}', Args: {}".format(
                    path.relative_to(self.topdir), method, cmd, args
                ),
            )

        action = ops.get((method, cmd))
        if action:
            action(path, args)
        else:
            raise HTUPLError(
                400,
                "Bad request",
                "{} {} cannot process.".format(method, path.relative_to(self.topdir)),
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
            self.compress(path, self.zip_archiver, ".zip", "application/zip")
        elif fmt == "tar.gz":
            self.compress(path, self.tar_archiver, ".tar.gz", "application/gzip")
        else:
            resp = self.response_json(
                400,
                "Bad request",
                {"extra": "Directory archive. Bad format {}".format(fmt)},
            )
            self.headers = [("Content-type", "application/json")]
            self.response = "400 Bad request"
            self.result = [json.dumps(resp, indent=2).encode()]

    def zip_archiver(self, path, fileobj):
        with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as zp:
            for dp, dirs, filenames in os.walk(path):
                for filename in filenames:
                    fullfile = pathlib.Path(dp) / filename
                    relfile = fullfile.relative_to(path)
                    zp.write(str(fullfile.resolve()), str(relfile))

    def tar_archiver(self, path, tfd):
        tarfd = tarfile.open(fileobj=tfd, mode="x:gz")
        with os.scandir(path) as scd:
            for entry in scd:
                fullname = path / entry.name
                relname = fullname.relative_to(path)
                tarfd.add(str(fullname), str(relname))
        tarfd.close()

    def mkdir(self, path, args):
        try:
            os.makedirs(path)
        except FileExistsError:
            resp = self.response_json(
                400,
                "Bad request",
                {
                    "extra": "Directory '{}' already exists".format(
                        path.relative_to(self.topdir)
                    )
                },
            )
            self.response = "400 Bad request"
            self.headers = [("Content-type", "application/json")]
            self.result = [json.dumps(resp, indent=2).encode()]

    def deldir(self, path, args):
        def handle_rmdir_errors(func, name, exc_info):
            info = traceback.format_exception(*exc_info)
            raise HTUPLError(500, "Cannot remove dir '{}'".format(name), info)

        shutil.rmtree(path, False, handle_rmdir_errors)

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
        fmt = args.get("format", ["zip"])[0]
        cmethods = {
            "gz": (self.gz_writer, ".gz", "application/gzip"),
            "zip": (self.zip_writer, ".zip", "application/zip"),
        }
        wrt, ext, mimetype = cmethods[fmt]
        self.compress(path, wrt, ext, mimetype)

    def compress(self, path, writer, ext, mimetype):
        tf = TemporaryFile()

        writer(path, tf)

        tf.seek(0, io.SEEK_END)
        size = tf.tell()
        tf.seek(0)
        name = path.name if path.name else "top"
        resp = self.response_json(200, "OK")
        self.response = "200 OK"
        self.headers = [
            ("Content-length", str(size)),
            ("Content-type", mimetype),
            ("Content-disposition", "attachment; filename=" + name + ext),
        ]
        self.result = FileWrapper(tf)

    def gz_writer(self, path, tf):
        with path.open("rb") as pfd:
            with gzip.GzipFile(mode="wb", fileobj=tf) as gz:
                chunk = pfd.read(CHUNKSIZE)
                while chunk:
                    gz.write(chunk)
                    chunk = pfd.read(CHUNKSIZE)

    def zip_writer(self, path, fileobj):
        with zipfile.ZipFile(fileobj, "w", zipfile.ZIP_DEFLATED) as zp:
            zp.write(str(path.resolve()), path.name)

    def file_info(self, path, args):
        st = path.stat()
        atime = datetime.fromtimestamp(st.st_atime, tz=LOCAL_TZ)
        mtime = datetime.fromtimestamp(st.st_mtime, tz=LOCAL_TZ)
        ctime = datetime.fromtimestamp(st.st_ctime, tz=LOCAL_TZ)
        data = {
            "name": path.name,
            "size": st.st_size,
            "human_size": human_size(st.st_size),
            "path": "/" + str(path.parent.relative_to(self.topdir)),
            "checksum": self.calc_sha256(path),
            "atime": atime.isoformat(),
            "mtime": mtime.isoformat(),
            "ctime": ctime.isoformat(),
        }
        rsp = self.response_json(200, "OK", data)
        self.response = "200 OK"
        self.headers = [("Content-type", "application/json")]
        self.result = [json.dumps(rsp, indent=2).encode()]

    def calc_sha256(self, path):
        chksum = hashlib.sha256()
        with path.open("rb") as fd:
            chunk = fd.read(CHUNKSIZE)
            while chunk:
                chksum.update(chunk)
                chunk = fd.read(CHUNKSIZE)
        return chksum.hexdigest()

    def checksum(self, path, args):
        match = args.get("match", [""])[0]
        data = {}
        data["checksum"] = self.calc_sha256(path)
        data["filename"] = path.name
        if match:
            data["match"] = match.lower() == data["checksum"]

        rsp = self.response_json(200, "OK", data)
        self.response = "200 OK"
        self.headers = [("Content-type", "application/json")]
        self.result = [json.dumps(rsp, indent=2).encode()]

    def upload(self, path, args):
        content_type = self.env.get("CONTENT_TYPE", "")
        if content_type.startswith("multipart/form-data"):
            fs = FieldStorage(fp=self.env["wsgi.input"], environ=self.env)
            for key in fs:
                if fs[key].file:
                    pn = path / fs[key].filename
                    with pn.open("wb") as saved:
                        while 1:
                            chunk = fs[key].file.read(CHUNKSIZE)
                            if len(chunk) > 0:
                                saved.write(chunk)
                            else:
                                break
        else:
            fp = self.env["wsgi.input"]
            content_length = int(self.env.get("CONTENT_LENGTH", "0"))
            if not content_length:
                raise HTUPLError(400, "Bad request", "No Content-length header")
            bytesleft = content_length
            with path.open("wb") as outfd:
                while bytesleft > 0:
                    chunk = fp.read(min(CHUNKSIZE, bytesleft))
                    bytesleft -= len(chunk)
                    if len(chunk) > 0:
                        outfd.write(chunk)

    def copy(self, path, args):
        relpath = path.relative_to(self.topdir)
        dest = args.get("dest", [""])[0]
        if not dest:
            raise HTUPLError(
                400,
                "Bad request",
                {
                    "extra": "Destination not specified.\ncmd='copy' path='/{}'".format(
                        relpath
                    )
                },
            )
        if dest[0] == "/":
            dest = dest[1:]
        destpath = self.topdir / dest
        reldest = destpath.resolve().relative_to(self.topdir)
        try:
            shutil.copyfile(path, destpath)
        except shutil.SameFileError:
            raise HTUPLError(
                400,
                "Bad request",
                {
                    "extra": "Destination and source are the same.\ncmd='copy' path='/{}' "
                    "dest='{}'".format(relpath, reldest)
                },
            )
        except OSError:
            raise HTUPLError(
                400,
                "Bad request",
                {
                    "extra": "Cannot write to destination.\ncmd='copy' path='/{}' "
                    "dest='{}'".format(path.relative_to(self.topdir), reldest)
                },
            )

    def move(self, path, args):
        relpath = path.relative_to(self.topdir)
        dest = args.get("dest", [""])[0]
        if not dest:
            raise HTUPLError(
                400,
                "Bad request",
                {
                    "extra": "Destination not specified.\ncmd='move' path='/{}'".format(
                        relpath
                    )
                },
            )
        if dest[0] == "/":
            dest = dest[1:]
        destpath = self.topdir / dest

        shutil.move(str(path), str(destpath))

    def delfile(self, path, args):
        path.unlink()


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
        html = resources["index.html"].format(startdir.relative_to(self.topdir))
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
        enc_fav = (
            b"000310RRvX5C8!H1OO-j000&M001Ze000mG001BW000311ONa4004"
            b"jh0000000000000mG0000000000004r5f&c>p0sz+5)&Kwi000000"
            b"00000000000000000000000000000000000000000000000000000"
            b"000000000000000000002A|fIpAOHXYA|fIpAOHXYA|fIpAOHXW00"
            b"0000000003sp)0000003sp)0000003sp)0000003sp)0000003sp)"
            b"00002A|fIpAOHXZA|fIpFaQ7m0U{z00000005T&00000000000009"
            b"6000960007_z007_z007_z00960008_y008_y008_y008_y008_y0"
            b"07_z007_z008(O008_y00960000"
        )
        favicon = base64.b85decode(enc_fav)
        headers = [
            ("Content-lenght", str(len(favicon))),
            ("Content-type", "image/x-icon"),
            ("Content-disposition", "attachment; filename=favicon.ico"),
        ]
        self.start_response("200 OK", headers)
        return [favicon]

    def check_valid(self, strpath):
        p = self.topdir / strpath
        try:
            relpath = p.relative_to(self.topdir)
        except ValueError:
            return False
        return relpath is not None

    def dispatch_api_call(self, apidict, method):
        version_tpl, APIClass = API.find_version(apidict["version"])
        api = APIClass(self.env, self.topdir, self.hidden_files)
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

    def send_static(self, fn):
        content = resources[fn].encode()
        mime, enc = mimetypes.guess_type(fn)
        if not mime:
            mime = "application/octet-stream"

        headers = [
            ("Content-length", str(len(content))),
            ("Content-type", mime),
            ("Content-disposition", "attachment; filename=" + fn),
        ]
        if enc:
            headers.append(("Content-encoding", enc))

        self.start_response("200 OK", headers)
        # return FileWrapper(pfile.open("rb"))
        return [content]

    def __call__(self, env, start_response):
        self.env = env
        self.start_response = start_response
        method = env["REQUEST_METHOD"]

        request = env.get("PATH_INFO", "")
        querystring = env.get("QUERY_STRING", "")

        if request == "/favicon.ico":
            return self.send_favicon()
        if request == "/app.css":
            return self.send_static("app.css")
        if request == "/app.js":
            return self.send_static("app.js")

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
            if not resource.is_file():
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


htmlpage = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>httpuploader</title>
    <link rel="stylesheet" href="app.css">
    <script src="app.js" language="javascript"></script>
  </head>
  <body>
    <input id="curdir" type="hidden" value="{}" />
    <div id="errorarea" class="invisible">
        <div id="errortxt"></div>
        <div id="errback">Back</div>
    </div>
    <div class="invisible boxed" id="dirinput">
        <span>Enter directory name:</span>
        <input id="direntry" type="text" name="dir" size="40" />
    </div>
    <div id="topstrip">
        <div class="toprowbutton" id="mkdirbtn">Create directory here</div>
        <label  class="toprowbutton">
            <input id="fileinput" multiple="" type="file">
            <div id="uplbtn" >Upload to this directory</div>
        </label>
        <div id="statusmsg"></div>
    </div>
    <h3 id="pgtitle"></h3>
    <div id="dirarea"></div>
    <div id="filearea"></div>
  </body>
</html>
"""

stylesheet = """
body {
    background: #007399;
    font-family: "arial", "helvetica", "sans-serif";
    font-size: large;
    color: #eeeeee;
}

a:link {
    color: #bbbbbb;
    text-decoration: none;
}

a:visited {
    color: #999999;
    text-decoration: none;
}

#topstrip {
    display: flex;
    width: 100%;
}

#dirarea {
    float:left;
    overflow: auto;
    min-width: 15%;
    max-width: 40%;
}

#filearea {
    overflow: auto;
    max-width: 90%;
}

#errorarea {
    background: #007399;
    overflow: auto;
    text-overflow: ellipsis;
    max-width: 90%;
    background: #3377ff;
    width: 25%;
    top: 50px;
    left: 50px;
    padding: 30px;
    border: 1px solid #cccccc;
    border-radius: 15px;
    position: fixed;
}

#errback {
  position: absolute;
  bottom: 10px;
  right: 10px;
  padding: 5px;
  background: #002699;
  color: #eeeeee;
  border-radius: 4px;
}

.diritem {
    background: #002699;
    min-width: 10%;
    max-width: 75%;
    overflow: hidden;
    text-overflow: ellipsis;
    display: inline-block;
    padding-top: 5px;
    padding-bottom: 5px;
    padding-left: 15px;
    padding-right: 15px;
    border-radius: 10px;
}

.fileitem {
    background: #002699;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 5px;
    padding-top: 5px;
    padding-bottom: 5px;
    padding-left: 15px;
    padding-right: 15px;
    border-radius: 10px;
}

.fileitem:hover, .diritem:hover {
    box-shadow: 0 5px 5px 0 #001a66;
}

.filesize {
    float: right;
}

.boxed {
    box-shadow: 0 5px 5px 0 #001a66;
}

input[type="file"] {
    display: none;
}

.invisible {
    display: none;
}

#dirinput {
    background: #007399;
    width: 25%;
    margin: 30px 0 0 30px;
    padding: 5px;
    position: absolute;
}


.toprowbutton {
    width: 10%;
    text-align: center;
    background: #3377ff;
    margin-top: 5px;
    margin-right: 5px;
    padding-top: 5px;
    padding-bottom: 5px;
    padding-left: 15px;
    padding-right: 15px;
    border-radius: 10px;
}

.toprowbutton:hover {
    background: "#6699ff";
    box-shadow: 0 5px 5px 0 #001a66;
}
"""

jsapp = """
(function(document, window, undefined) {

    window.onload = function() {

        var curdir = document.getElementById('curdir');
        //var topstrip = document.getElementById('topstrip');
        var fileSelect = document.getElementById('fileinput');
        var topRowBtns = document.getElementsByClassName('toprowbutton');
        var msg = document.getElementById('statusmsg');
        var mkdirbtn = document.getElementById('mkdirbtn');
        var uplbtn = document.getElementById('uplbtn');
        var dirinput = document.getElementById('dirinput');
        var direntry = document.getElementById('direntry');
        var pgtitle = document.getElementById('pgtitle');
        var dirarea = document.getElementById('dirarea');
        var filearea = document.getElementById('filearea');
        var errorarea = document.getElementById('errorarea');
        var errortxt = document.getElementById('errortxt');
        var errback = document.getElementById('errback');

        function fill_filearea(tagid, filelist) {
            tagid.innerText = "";
            for (var i=0; i<filelist.length; i++) {
                    var name = filelist[i].name,
                        link = filelist[i].links.download.href,
                        size = filelist[i].size,
                        div = document.createElement("div"),
                        anchor = document.createElement("a"),
                        szelm = document.createElement("span");
                    div.setAttribute("class", "fileitem");
                    //setBoxable(div);
                    szelm.setAttribute("class", "filesize");
                    szelm.innerText = size;
                    anchor.setAttribute("href", link);
                    anchor.innerText = name;
                    div.appendChild(anchor);
                    div.appendChild(szelm);
                    tagid.appendChild(div);
            }
        }

        function fill_dirarea(tagid, dirlist) {
            tagid.innerText = "";
            for (var i=0; i<dirlist.length; i++) {
                var name = dirlist[i].name,
                    link = dirlist[i].links.list.href;
                var div = document.createElement("div");
                var anchor = document.createElement("a");
                div.setAttribute("class", "diritem");
                //setBoxable(div);
                anchor.setAttribute("href", link)
                anchor.innerText = name;
                anchor.addEventListener('click', function(evnt) {
                    var a = evnt.target;
                    evnt.preventDefault();
                    var url = a.href;
                    setup_page(url);
                });
                div.appendChild(anchor);
                tagid.appendChild(div);
                tagid.appendChild(document.createElement("br"));
            }
        }

        function fill_errorarea(tagid, errobj) {
            tagid.innerText = "";
            var h3 = document.createElement("h3"),
                pre = document.createElement("pre");
            h3.innerText = errobj.rc + " " + errobj.msg;
            pre.innerText = errobj.data.extra;
            tagid.appendChild(h3);
            tagid.appendChild(pre);
        }

        function setup_page(url) {
            var xhr = new XMLHttpRequest();
            xhr.open("GET", url);
            xhr.onload = function() {
                var jsnobj = JSON.parse(xhr.response);
                if (xhr.status == 200) {
                    var dirname = jsnobj.data.name;
                    var reldir = dirname.charAt(0) === '/' ?
                                    dirname.substring(1): dirname;
                    pgtitle.innerText = "Contents of " + dirname;
                    curdir.value = reldir;
                    fill_filearea(filearea, jsnobj.data.files);
                    fill_dirarea(dirarea, jsnobj.data.directories);
                } else if (xhr.status >= 400) {
                    fill_errorarea(errortxt, jsnobj);
                    errorarea.classList.toggle("invisible");
                }
            };
            xhr.send();
        }

        mkdirbtn.addEventListener('click', function(event) {
            dirinput.classList.toggle("invisible");
        });

        errback.addEventListener('click', function(event) {
            errorarea.classList.toggle("invisible");
        });

        dirinput.addEventListener('change', function(event) {
            var dirname = direntry.value;
            dirinput.classList.toggle("invisible");
            direntry.value = '';
            var url = "/api/1/" + curdir.value + "/" + dirname + "?cmd=mkdir";
            var xhr = new XMLHttpRequest();
            xhr.open('POST', url);
            xhr.onload = function() {
                if (xhr.status < 400) {
                    //pgtitle.dataset.curdir = '';
                    //window.location.reload(true);
                    var apidir = "/api/1/" + curdir.value + "?cmd=list";
                    setup_page(apidir);
                } else {
                    var jsnobj = JSON.parse(xhr.response);

                    fill_errorarea(errortxt, jsnobj);
                    errorarea.classList.toggle("invisible");
                }
            };
            xhr.send();
        });

        fileSelect.addEventListener('change', function(event) {
            event.preventDefault();

            var fileList = fileSelect.files;
            if (fileList && fileList.length == 0) {
                return;
            }

            var formData = new FormData();
            for (var i=0; i<fileList.length; i++) {
                var f = fileList[i];
                formData.append("files_"+i, f, f.name);
            }
            xhr = new XMLHttpRequest();
            xhr.open('POST', curdir.value);
            xhr.onload = function() {
                if (xhr.status < 400) {
                    //uploadButton.innerHTML = "Upload";
                    var apidir = "/api/1/" + curdir.value + "?cmd=list";
                    setup_page(apidir);
                } else {
                    var jsnobj = JSON.parse(xhr.response);

                    fill_errorarea(errortxt, jsnobj);
                    errorarea.classList.toggle("invisible");
                }
            };
            xhr.send(formData);
        }, true);

        setup_page("/api/1/"+curdir.value);
    };

})(document, window);
"""

resources = {"index.html": htmlpage, "app.js": jsapp, "app.css": stylesheet}


if __name__ == "__main__":
    sys.exit(main())
