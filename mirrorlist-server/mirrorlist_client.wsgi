#!/usr/bin/python
#
# Copyright (c) 2007 Dell, Inc.
#  by Matt Domsch <Matt_Domsch@dell.com>
# Licensed under the MIT/X11 license

import socket, select
import cPickle as pickle
from string import zfill, atoi, strip, replace
from paste.wsgiwrappers import *
import gzip
import cStringIO
from datetime import datetime, timedelta

socketfile = '/var/run/mirrormanager/mirrorlist_server.sock'
request_timeout = 60 # seconds

def get_mirrorlist(d):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(socketfile)
    except:
        raise

    p = pickle.dumps(d)
    del d
    size = len(p)
    s.sendall(zfill('%s' % size, 10))

    # write the pickle
    s.sendall(p)
    s.shutdown(socket.SHUT_WR)
    del p

    # wait for other end to start writing
    expiry = datetime.utcnow() + timedelta(seconds=request_timeout)
    rlist, wlist, xlist = select.select([s],[],[],request_timeout)
    if len(rlist) == 0:
        s.shutdown(socket.SHUT_RD)
        raise socket.timeout
    
    readlen = 0
    resultsize = ''
    while readlen < 10:
        resultsize += s.recv(10 - readlen)
        readlen = len(resultsize)
    resultsize = atoi(resultsize)

    readlen = 0
    p = ''
    while readlen < resultsize and datetime.utcnow() < expiry:
        p += s.recv(resultsize - readlen)
        readlen = len(p)
    results = pickle.loads(p)
    del p

    s.shutdown(socket.SHUT_RD)
    return results

def real_client_ip(xforwardedfor):
    """Only the last-most entry listed is the where the client
    connection to us came from, so that's the only one we can trust in
    any way."""
    return xforwardedfor.split(',')[-1].strip()

def request_setup(request):
    fields = ['repo', 'arch', 'country', 'path', 'netblock']
    d = {}
    request_data = request.GET
    for f in fields:
        if f in request_data:
            d[f] = strip(request_data[f])
            # add back '+' that were converted to ' ' by util.FieldStorage
            if f == 'path':
                d[f] = replace(d[f], ' ', '+')

    if 'ip' in request_data:
        client_ip = strip(request_data['ip'])
    elif 'X-Forwarded-For' in request.headers:
        client_ip = real_client_ip(strip(request.headers['X-Forwarded-For']))
    else:
        client_ip = request.environ['REMOTE_ADDR']
    d['client_ip'] = client_ip

    d['metalink'] = False
    scriptname = ''
    pathinfo = ''
    if 'SCRIPT_NAME' in request.environ:
        scriptname = request.environ['SCRIPT_NAME']
    if 'PATH_INFO' in request.environ:
        pathinfo = request.environ['PATH_INFO']
    if scriptname ==  '/metalink' or pathinfo == '/metalink':
        d['metalink'] = True

    for k, v in d.iteritems():
        try:
            d[k] = unicode(v, 'utf8', 'ignore').encode('utf8')
        except:
            pass
    return d

def accept_encoding_gzip(request):
    for h in request.headers:
        if h.lower() == 'accept-encoding':
            if 'gzip' in request.headers[h]:
                return True
    return False

def gzipBuf(buf):
    zbuf = cStringIO.StringIO()
    zfile = gzip.GzipFile(fileobj = zbuf, compresslevel = 9, mode='wb')
    zfile.write(buf)
    zfile.close()
    return zbuf.getvalue()

def manage_compression(request, response, buf):
    if accept_encoding_gzip(request):
        buf = gzipBuf(buf)
        response.headers['Content-Encoding'] = 'gzip'
    return buf

def application(environ, start_response):
    request = WSGIRequest(environ)
    response = WSGIResponse()

    d = request_setup(request)

    try:
        r = get_mirrorlist(d)
        message=r['message']
        resulttype=r['resulttype']
        results = r['results']
        returncode = r['returncode']
    except: # most likely socket.error, but we'll catch everything
        response.status_code=503
        return response(environ, start_response)

    if resulttype == 'mirrorlist':
        # results look like [(hostid, url), ...]
        if 'redirect' in request.GET:
            if len(results) == 0:
                response.status_code=404
                return response(environ, start_response)
            else: 
                (hostid, url) = results[0]
                response.status_code=302
                response.headers['Location'] = str(url)
                return response(environ, start_response)

        text = ""
        text += message + '\n'
        for (hostid, url) in results:
            text += url + '\n'
        results = text
        response.headers['Content-Type'] = "text/plain"
    elif resulttype == 'metalink':
        # results are an XML document
        response.headers['Content-Type'] = "application/metalink+xml"
    else:
        response.headers['Content-Type'] = "text/plain"

    results = results.encode('utf-8')
    results = manage_compression(request, response, results)
    response.headers['Content-Length'] = str(len(results))
    response.write(results)
    return response(environ, start_response)


if __name__ == '__main__':
    from paste import httpserver
    httpserver.serve(application, host='127.0.0.1', port='8090')
