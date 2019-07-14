#!/usr/bin/env python3
"""Simple directory listing http server that also allows to upload
files.
"""

import os
import sys
import argparse
import base64
import mimetypes
import pathlib
import traceback
from cgi import FieldStorage
from wsgiref.util import FileWrapper

CHUNKSIZE = 65536    # 64KB

options = {
    "rootdir": pathlib.Path(".").resolve(),
}


jspattern = """<script>
    (function(document, window, undefined) {

        function boxToggle(event) {
                    if (event.target.tagName == "DIV") {
                        event.target.classList.toggle("boxed");
                    }
                }
        function setBoxable(itemlst) {
            for (var i = 0; i < itemlst.length; i++) {
                var item = itemlst[i];
                item.addEventListener('mouseenter', boxToggle, true);
                item.addEventListener('mouseleave', boxToggle, true);
            }
        }


        var fileSelect = document.getElementById('fileinput');
        var topRowBtns = document.getElementsByClassName('toprowbutton');
        var msg = document.getElementById('statusmsg');
        var curdir = document.getElementById('curdir');
        var mkdirbtn = document.getElementById('mkdirbtn');
        var dirinput = document.getElementById('dirinput');
        var sendmkdir = document.getElementById('sendmkdir');

        for (var i = 0; i < topRowBtns.length; i++) {
            (function(button) {
                button.addEventListener('mouseenter', function() {
                        button.style.background = "#6699ff";
                        button.classList.toggle("boxed");
                    }, true);
                button.addEventListener('mouseleave', function() {
                        button.style.background = "#3377ff";
                        button.classList.toggle("boxed");
                    }, true);
            })(topRowBtns[i]);
        }

        setBoxable(document.getElementsByClassName("diritem"));
        setBoxable(document.getElementsByClassName("fileitem"));

        mkdirbtn.addEventListener('click', function(event) {
            dirinput.classList.toggle("invisible");
        });

        dirinput.addEventListener('change', function(event) {
            var dirname = dirinput.value;
            dirinput.classList.toggle("invisible");
            dirinput.value = '';
            sendmkdir.submit();
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
                if (xhr.status == 200) {
                    //uploadButton.innerHTML = "Upload";
                    window.location.reload(true);
                } else {
                    msg.innerHTML = "Error: " + xhr.status
                                    + " " + xhr.statusText;
                }
            };
            xhr.send(formData);
        }, true);

    })(document, window)
</script>
"""

csspattern = """<style>
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
    min-width: 10%;
    max-width: 40%;
}

#filearea {
    overflow: auto;
    max-width: 60%;
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
</style>
"""

def errorpage(title, name, brief, extended=""):

    pattern = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
{4}
<title>{0}</title>
</head>
<body>
<h2>{0}</h2>
<p>'{1}' {2}</p>
<hr>
<pre>{3}</pre>
<hr>
[ <a href="/">Start</a> ]
</body>
</html>
"""
    return pattern.format(title, name, brief, extended, csspattern).encode()


def dirlstpage(pth, dirs, files):
    pattern = """<!doctype html>
<html lang=en>
<head>
<meta charset=utf-8>
{4}
<title>Contents of {0}</title>
</head>
<body>
<div class="invisible boxed" id="dirinput">
    <form id="sendmkdir" action="/mkdir" method="post">
    <span>Enter directory name:</span>
    <input type="text" name="dir" size="40" />
    <input id="curdir" type="hidden" name="curdir" value="{0}" />
    </form>
</div>
<div id="topstrip">
    <div class="toprowbutton" id="mkdirbtn">Create directory here</div>
    <input type="hidden" name="curdir" value="." />
    <label  class="toprowbutton">
        <input id="fileinput" multiple="" type="file">
        <div id="uplbtn" >Upload to this directory</div>
    </label>
    <div id="statusmsg"></div>
</div>
<h3>Contents of '{0}'</h3>
<div id='dirarea'>
<div class='diritem'><a href="{1}">..</a></div><br>
{2}
</div>
<div id='filearea'>
{3}
</div>
{5}
</body>
</html>
"""
    dirptn = "<div class='diritem'><a href='{0!s}'>{1!s}</a></div><br>\n"
    fileptn = "<div class='fileitem'><a href='{0!s}'>{1!s}</a>&nbsp;<span class='filesize'>{2}</span></div>\n"
    rootfull = options["rootdir"].resolve()
    if pth == rootfull:
        updir = "/"
    else:
        updir = "/" / pth.parents[0].relative_to(rootfull)

    relpath = pth.relative_to(rootfull)

    htdirlst = ""
    for dir1 in dirs:
        full = "/" / relpath / dir1
        htdirlst += dirptn.format(full, dir1)

    htfilelst = ""
    for fil1, sz in files:
        full =  "/" / relpath / fil1
        htfilelst += fileptn.format(full, fil1, sz)

    return pattern.format("/" / relpath, updir, htdirlst, htfilelst, csspattern, jspattern).encode()


def send_dirlist(startresp, pth):
    try:
        pth, dirs, files = contents(pth)
    except Exception:
        x, m, tb = sys.exc_info()
        startresp("500 Bad Gateway", [])
        return [ errorpage("500 Bad Gateway", x.__name__, str(m),
                            "".join(traceback.format_tb(tb))) ]
    startresp("200 OK", [])
    return [ dirlstpage(pth, dirs, files) ]


def send_file(startresp, pfile):
    """
    Send a file to the client
    """
    stinfo = pfile.stat()
    mime, enc = mimetypes.guess_type(str(pfile))
    if not mime:
        mime = "application/octet-stream"

    headers = [ ("Content-length", str(stinfo.st_size)),
                ("Content-type", mime),
                ("Content-disposition", "attachment; filename=" + pfile.name),
              ]
    if enc:
        headers.append( ("Content-encoding", enc) )

    startresp("200 OK", headers)
    return FileWrapper(pfile.open("rb"))

def send_favicon(startresp):
    enc_fav = (b'000310RRvX5C8!H1OO-j000&M001Ze000mG001BW000311ONa4004'
               b'jh0000000000000mG0000000000004r5f&c>p0sz+5)&Kwi000000'
               b'00000000000000000000000000000000000000000000000000000'
               b'000000000000000000002A|fIpAOHXYA|fIpAOHXYA|fIpAOHXW00'
               b'0000000003sp)0000003sp)0000003sp)0000003sp)0000003sp)'
               b'00002A|fIpAOHXZA|fIpFaQ7m0U{z00000005T&00000000000009'
               b'6000960007_z007_z007_z00960008_y008_y008_y008_y008_y0'
               b'07_z007_z008(O008_y00960000')
    favicon = base64.b85decode(enc_fav)
    headers = [ ("Content-lenght", str(len(favicon))),
                ("Content-type", "image/x-icon"),
                ("Content-disposition", "attachment; filename=favicon.ico") ]
    startresp("200 OK", headers)
    return [ favicon ]

def human_size(size):
    units = [ "KB", "MB", "GB", "TB" ]
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


def contents(pdir):
    dirs = []
    files = []
    lst = os.scandir(str(pdir))
    for entry in lst:
        if entry.is_dir():
            dirs.append(entry.name)
        else:
            stat = entry.stat()
            szstr = human_size(stat.st_size)
            files.append( (entry.name, szstr) )
    return pdir, sorted(dirs), sorted(files)


def get_uploaded_files(startresp, env, path):
    fs = FieldStorage(fp=env["wsgi.input"], environ=env)
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
    return send_dirlist(startresp, path)


def send_mkdir(startresp, env, root):
    fs = FieldStorage(fp=env["wsgi.input"], environ=env)
    if "dir" not in fs or "curdir" not in fs:
        startresp("400 Bad Request", [])
        return [ errorpage("400 Bad Request", "Incorrect form",
            "Form posted with missing fields.", "") ]

    dir = fs["dir"].value
    curdir = fs["curdir"].value
    if curdir.startswith(os.sep):
        curdir = curdir[1:]

    newdir = root / curdir / dir
    return create_directory(startresp, env, newdir, fs["curdir"].value)


def create_directory(startresp, env, path, curdir):
    try:
        path.mkdir()
    except Exception:
        x, m, tb = sys.exc_info()
        startresp("500 Bad Gateway", [])
        return [ errorpage("500 Bad Gateway", x.__name__, str(m),
                            "".join(traceback.format_tb(tb))) ]

    if curdir[0] == '.':
        curdir = '/' + curdir[1:]
    startresp("303 See other",
                [("Location", curdir), ])
    return []


def send_error(startresp, *args):
    startresp(args[0], [])
    return [ errorpage(*args) ]


def check_valid(pdir):
    """
    Check that the pdir pathname resolves to something under rootdir
    """
    p = options["rootdir"] / pdir
    p = p.resolve()
    rootfull = options["rootdir"].resolve()
    return rootfull == p or rootfull in p.parents


def ul_serve(env, start_response):
    """
    WSGI application
    """
    method = env["REQUEST_METHOD"]
    target = env.get("PATH_INFO").strip("/")

    if target == "favicon.ico":
        return send_favicon(start_response)

    if target == "mkdir":
        return send_mkdir(start_response, env, options["rootdir"])

    pth = pathlib.Path(target)
    if not check_valid(pth):
        return send_error(start_response, "401 FORBIDDEN", target, "Not a valid pathname")

    fullpath = ( options["rootdir"] / pth ).resolve()

    if method == "GET":
        if fullpath.is_file():
            return send_file(start_response, fullpath)
        else:
            return send_dirlist(start_response, fullpath)

    if method == "POST":
        if fullpath.is_dir():
            return get_uploaded_files(start_response, env, fullpath)
        else:
            return send_error(start_response, "400 BAD REQUEST", target, "Cannot upload to a file.")


def get_cli_arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("-d", "--rootdir", metavar="DIR", default=".",
                type=pathlib.Path,
                help="set the root of the directory hierarchy to DIR")
    parser.add_argument("-p", "--port", metavar="PORT", default=8018,
                type=int, help="listen for connections on specified PORT")

    return parser.parse_args(argv)


def main(argv=None):
    from wsgiref.simple_server import make_server, WSGIServer
    from socketserver import ThreadingMixIn
    class MTServer(ThreadingMixIn, WSGIServer): pass

    if argv is None:
        argv = sys.argv[1:]

    args = get_cli_arguments(argv)
    port = options["port"] = args.port
    options["rootdir"] = args.rootdir
    srv = make_server("", port, ul_serve, server_class=MTServer)
    print("Listening on port {0}".format(port), file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
