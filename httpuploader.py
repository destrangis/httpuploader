import os
import sys
import mimetypes
import pathlib
import traceback
from cgi import FieldStorage
from wsgiref.util import FileWrapper


CHUNKSIZE = 65536    # 64KB

options = {
    "rootdir": pathlib.Path("."),
}


jspattern = """ <script>
            (function(document, window, undefined) {
                console.log('In script');
                var form = document.getElementById('formupload');
                var fileSelect = document.getElementById('fileinput');
                var uploadButton = document.getElementById('uplbtn');
                var msg = document.getElementById('statusmsg');

                form.addEventListener('submit', function(event) {
                    event.preventDefault();
                    console.log("Caught event");
                    uploadButton.innerHTML = "Uploading...";

                    var fileList = fileSelect.files;
                    var formData = new FormData();
                    for (var i=0; i<fileList.length; i++) {
                        var f = fileList[i];
                        formData.append("files_"+i, f, f.name);
                    }
                    xhr = new XMLHttpRequest();
                    xhr.open('POST', form.action);
                    xhr.onload = function() {
                        if (xhr.status == 200) {
                            uploadButton.innerHTML = "Upload";
                            window.location.reload(true);
                        } else {
                            msg.innerHTML = "Error: " + xhr.status
                                            + " " + xht.statusText;
                        }
                    };
                    xhr.send(formData);
                }, true);

            })(document, window)
        </script>
"""

csspattern = """ """

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
<div id='topstrip'>
 <!-- <form id="formupload" action="upload" method="POST" enctype="multipart/form-data"> -->
    <form id="formupload" action="{0}" method="POST" enctype="multipart/form-data">
        Choose files: <input type="file" id="fileinput" multiple /><br>
        <button type="submit" id="uplbtn">Upload</button>
    </form>
    <div id="statusmsg"></div>
</div>
<h3>Contents of {0}</h3>
[ <a href="{1}">..</a> ]<br>
{2}
{3}
{5}
</body>
</html>
"""
    if pth == options["rootdir"]:
        updir = "/"
    else:
        updir = "/" / pth.parents[0]

    htdirlst = ""
    for dir1 in dirs:
        full = "/" / pth / dir1
        htdirlst += "[ <a href='{0!s}'>{1!s}</a> ]<br>\n".format(full, dir1)

    htfilelst = ""
    for fil1 in files:
        full = "/" / pth / fil1
        htfilelst += "<a href='{0!s}'>{1!s}</a><br>\n".format(full, fil1)

    return pattern.format(pth, updir, htdirlst, htfilelst, csspattern, jspattern).encode()


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



def contents(pdir):
    dirs = []
    files = []
    with os.scandir(pdir) as lst:
        for entry in lst:
            if entry.is_dir():
                dirs.append(entry.name)
            else:
                files.append(entry.name)
    return pdir, dirs, files


def get_files(startresp, env, path):
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
    if target == "":
        target = options["rootdir"]

    pth = pathlib.Path(target)
    if not check_valid(pth):
        return send_error(start_response, "401 FORBIDDEN", target, "Not a valid pathname")

    fullpath = ( options["rootdir"] / pth ).resolve()

    if method == "GET":
        if fullpath.is_file():
            return send_file(start_response, fullpath)
        else:
            return send_dirlist(start_response, pth)

    if method == "POST":
        if fullpath.is_dir():
            return get_files(start_response, env, fullpath)
        else:
            return send_error(start_response, "400 BAD REQUEST", target, "Cannot upload to a file.")


if __name__ == "__main__":
    from wsgiref.simple_server import make_server
    with make_server("", 8018, ul_serve) as srv:
        srv.serve_forever()
