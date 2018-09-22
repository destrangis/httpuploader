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

Httpuploader is a single file with **no dependencies** outside the
Python Standard Library. It is a WSGI application so that it can be
imported as a module (the name is ``httpuploader.ul_serve``) and run
in any WSGI compliant server like mod_wsgi, Rocket, uWSGI, etc.

The client's browsers need Javascript activated. It won't work in old
versions of Internet Explorer though. Get the latest version or Edge.
