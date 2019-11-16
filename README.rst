HTTPUPLOADER
============

An HTTP server that displays a directory listing, much like Python's
default http.server module, except this one allows directory creation
and file upload by the user.

This can be useful anytime you want to quickly share files a directory,
for example in a classroom where the students need to obtain one or
more files from the instructor and need to upload their exercise.
In that case the instructor finds out their machine's IP address,
communicates the url ``http://ipaddress:port/`` to the students, opens
a command window and runs httpuploader like this::

   $ python3 httpuploader.py -p port -d directory

and they now have a quick server.

.. image:: Screenshot_httpuploader.png

Httpuploader is a single file with **no dependencies** outside the
Python Standard Library. It is a WSGI application so that it can be
imported as a module and run
in any WSGI compliant server like mod_wsgi, Rocket, uWSGI, etc. just by
instantiating the ``WSGIApp`` class with the top directory to serve and
a boolean value telling it whether to show hidden files and directories.

The client's browsers need Javascript activated. It won't work in old
versions of Internet Explorer though. Get the latest version or Edge.


Installing
----------

An install is not really necessary since, being a single file, it can be
just dropped anywhere convenient. However a ``setup.py`` program is provided
for convenience so that it can be downloaded from
`PyPI.org <https://pypi.org>`_ using ``pip``::

    $ pip3 install httpuploader

It can also be installed using ``setup.py`` as follows::

    $ python3 setup.py install


API
---

This version contains a RESTful API that can be used by other applications
as a remote file manager, allowing not only to upload and download files
and create directories, but to delete files and entire trees, calculate
checksums, download compressed files and compressed archive directories,
and copy and move on the same or different directories.

The API responds always with a JSON object with the following structure::

    {
        "version": <the API version>,
        "rc": <the status code, e.g. 404>,
        "msg": <the status message, e.g. "Not found">,
        "data": <a json object containing the response of the call>
    }

In the case of errors, the ``data`` field contains an object, containing
a field named ``extra`` with an extended explanation of the error.

The API calls have the form ``/api/<version>/resource?cmd=<command>&<args...>``
where ``<version>`` is the API version number, can be just ``1``, ``1.0`` or
``1.0.0``. At this moment, it is the only version available, but version
``1`` will indicate the highest API version available of the ``1.x.x``
range.

Available Commands
~~~~~~~~~~~~~~~~~~

Directory Operations
....................

list
,,,,

List the contents of a directory. Does not need to be specified::

    GET /api/1/<directory>?cmd=list
    GET /api/1/<directory>

The response data field contains the following fields:

name:
    Name of the directory
files:
    List of file objects
directories:
    List of directories
links:
    List of possible operations

archive
,,,,,,,

Download a compressed archive with the contents of the directory::

    GET /api/1/<directory>?cmd=archive&format=<zip|tar.gz>


mkdir
,,,,,

Create new directory::

    POST /api/1/<directory>?cmd=mkdir

If successful, the response will be ``204 No content``

upload
,,,,,,

Upload file(s) to directory::

    POST /api/1/<directory>?cmd=upload

The files can be uploaded by simply sending the file in the body of the
request, or sending several files in a multipart message.

If successful, the response will be ``204 No content``

delete
,,,,,,

Remove the directory::

    DELETE /api/1/<directory>

This command removes the entire tree without asking for confirmation. Use
with caution.

If successful, the response will be ``204 No content``


File Operations
...............

download
,,,,,,,,

Download a file::

    GET /<path>
    GET /api/1/<path>
    GET /api/1/<path>?cmd=download

It is not necessary to specify the command or the API/version prefix.
The mime type of the file is guessed based on the name using the Python
Standard Library ``mimetypes`` module.

info
,,,,

Miscellaneous information about a file::

    GET /api/<version>/path?cmd=info

Upon success, the response ``data`` field will contain a JSON object with
the following files:

name:
    The name of the file.
size:
    The size in bytes, as a number.
human_size:
    The size in a string, expressed in the easiest to read unit.
path:
    Path from the top directory.
checksum:
    The sha256 checksum.
atime:
    Last access time, as a string in ISO format.
mtime:
    Last modification time, as a string in ISO format.
ctime:
    Creatrion time, as a string in ISO format.

Example::

    GET /api/1/opt/jdk1.8.0_51/COPYRIGHT?cmd=info
    {
      "rc": 200,
      "msg": "OK",
      "api_version": "1.0.0",
      "data": {
        "name": "COPYRIGHT",
        "size": 3244,
        "human_size": "3.17 KB",
        "path": "/opt/jdk1.8.0_51",
        "checksum": "89471aea3957922df21c7088d2687c4e43f5ff14e635e7d971083dde540b45e3",
        "atime": "2019-11-15T23:33:56.430384+00:00",
        "mtime": "2015-06-09T02:37:58+00:00",
        "ctime": "2015-07-20T18:17:40.394882+00:00"
      }
    }

compress
,,,,,,,,

Compress and download a single file::

    GET /api/1/<path>?cmd=compress&format=<zip|tar.gz>

checksum
,,,,,,,,

Compute, and optionally check, the SHA256 checksum of a file::

    GET /api/1/<path>?cmd=checksum
    GET /api/1/<path>?cmd=checksum&match=<checksum>

If no arguments are given the checksum of the file is computed and
returned in the ``data`` field of the JSON response, which has the
following fields:

filename:
    The base filename for which the checksum was computed.
checksum:
    The SHA256 digest as a lower case hexadecimal number.

If the ``match`` argument was used, it is used to compare it to the
computed checksum, and the following field is returned in addition to
the above:

match:
    A boolean indicating whether the checksums match.

Example::

    GET /api/1/opt/jdk1.8.0_51/COPYRIGHT?cmd=checksum&match=89471aea3957922df21c7088d2687c4e43f5ff14e635e7d971083dde540b45e3
    {
      "rc": 200,
      "msg": "OK",
      "api_version": "1.0.0",
      "data": {
        "checksum": "89471aea3957922df21c7088d2687c4e43f5ff14e635e7d971083dde540b45e3",
        "filename": "COPYRIGHT",
        "match": true
      }
    }


copy
,,,,

Copy a file::

    POST /api/1/<path>?cmd=copy&dest=<newfile>

If successful, the response will be ``204 No content``


move
,,,,

Move or rename a file::

    POST /api/1/<path>?cmd=move&dest=<newfile>

If successful, the response will be ``204 No content``

License
-------
This software is released under the **MIT License**
