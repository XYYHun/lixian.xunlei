#/bin/usr/env python
#encoding: utf8
#author: binux<17175297.hk@gmail.com>

import re
import time
import json
import urllib
import logging
import requests
from hashlib import md5
from random import random
from urlparse import urlparse
from pprint import pformat
from BeautifulSoup import BeautifulSoup
from jsfunctionParser import parser_js_function_call

DEBUG = logging.debug

class LiXianAPIException(Exception): pass
class NotLogin(LiXianAPIException): pass
class HTTPFetchError(LiXianAPIException): pass

def hex_md5(string):
    return md5(string).hexdigest()

def parse_url(url):
    url = urlparse(url)
    return dict([part.split("=") for part in url[4].split("&")])

def is_bt_task(task):
    return task.get("f_url", "").startswith("bt:")

class LiXianAPI(object):
    DEFAULT_USER_AGENT = 'User-Agent:Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/535.2 (KHTML, like Gecko) Chrome/15.0.874.106 Safari/535.2'
    DEFAULT_REFERER = 'http://lixian.vip.xunlei.com/'
    def __init__(self, user_agent = DEFAULT_USER_AGENT, referer = DEFAULT_REFERER):
        self.session = requests.session()
        self.session.headers['User-Agent'] = user_agent
        self.session.headers['Referer'] = referer

        self.islogin = False
        self.task_url = None
        self.uid = 0
        self.username = ""

    LOGIN_URL = 'http://login.xunlei.com/sec2login/'
    def login(self, username, password):
        self.username = username
        verifycode = self._get_verifycode(username)
        login_data = dict(
                u = username,
                p = hex_md5(hex_md5(hex_md5(password))+verifycode.upper()),
                verifycode = verifycode,
                login_enable = 1,
                login_hour = 720)
        r = self.session.post(self.LOGIN_URL, login_data)
        if r.error:
            r.raise_for_status()
        DEBUG(pformat(r.content))

        self._redirect_to_user_task()
        self.islogin = self.check_login()
        return self.islogin

    @property
    def _now(self):
        return int(time.time()*1000)

    @property
    def _random(self):
        return str(self._now)+str(random()*(2000000-10)+10)

    CHECK_URL = 'http://login.xunlei.com/check?u=%(username)s&cachetime=%(cachetime)d'
    def _get_verifycode(self, username):
        r = self.session.get(self.CHECK_URL %
                {"username": username, "cachetime": self._now})
        if r.error:
            r.raise_for_status()
        #DEBUG(pformat(r.content))

        verifycode_tmp = r.cookies['check_result'].split(":", 1)
        assert len(verifycode_tmp) == 2
        return verifycode_tmp[1]

    REDIRECT_URL = "http://dynamic.lixian.vip.xunlei.com/login"
    def _redirect_to_user_task(self):
        r = self.session.get(self.REDIRECT_URL)
        if r.error:
            r.raise_for_status()
        soup = BeautifulSoup(r.content)
        gdriveid_input = soup.find("input", attrs={'id' : "cok", "type": "hidden"})
        self.gdriveid = gdriveid_input.attrMap["value"]
        return r.url

    def _get_task_list(self, pagenum, st):
        r = self.session.get(self.task_url+"&st="+str(st), cookies=dict(pagenum=str(pagenum)))
        if r.error:
            r.raise_for_status()
        soup = BeautifulSoup(r.content)
        gdriveid_input = soup.find("input", attrs={'id' : "cok", "type": "hidden"})
        self.gdriveid = gdriveid_input.attrMap["value"]

        result = []
        for task in soup.findAll("div", **{"class": "rw_list"}):
            tmp = dict()
            for each in task.findAll("input"):
                input_id = each.get("id", "")
                if not input_id: continue
                input_attr = input_id.rstrip("1234567890")
                input_value = each.get("value", "")
                tmp[input_attr] = input_value
            assert tmp["input"]
            process = task.find("em", **{"class": "loadnum"})
            assert process.string
            tmp["process"] = float(process.string.rstrip("%"))
            result.append(tmp)
        DEBUG(pformat(result))
        return result

    d_status = { 0: "waiting", 1: "downloading", 2: "finished", 3: "failed", 5: "paused" }
    d_tasktype = {0: "bt", 1: "normal", 2: "ed2k", 3: "thunder", 4: "magnet" }
    st_dict = {"all": 0, "downloading": 1, "finished": 2}
    def get_task_list(self, pagenum=10, st=0):
        if isinstance(st, basestring):
            st = self.st_dict[st]
        raw_data = self._get_task_list(pagenum, st)
        result = []
        for r in raw_data:
            tmp = dict(
                    task_id=int(r["input"]),
                    cid=r['dcid'],
                    url=r["f_url"],
                    taskname=r["taskname"],
                    task_type=self.d_tasktype.get(int(r["d_tasktype"]), 1),
                    status=self.d_status.get(int(r["d_status"]), "waiting"),
                    process=r["process"],
                    lixian_url=r["dl_url"],
                    size=int(r["ysfilesize"]),
                    format=r["openformat"],
                  )
            result.append(tmp)
        return result

    QUERY_URL = "http://dynamic.cloud.vip.xunlei.com/interface/url_query?callback=queryUrl&u=%(url)s&random=%(random)s&tcache=%(cachetime)d"
    def bt_task_check(self, url):
        r = self.session.get(self.QUERY_URL % {"url": urllib.quote_plus(url),
                              "random": self._random,
                              "cachetime": self._now})
        if r.error:
            r.raise_for_status()
        #queryUrl(flag,infohash,fsize,bt_title,is_full,subtitle,subformatsize,size_list,valid_list,file_icon,findex,random)
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        if len(args) < 12:
            return {}
        result = dict(
                flag = args[0],
                cid = args[1],
                size = args[2],
                title = args[3],
                is_full = args[4],
                subtitle = args[5],
                subformatsize = args[6],
                size_list = args[7],
                valid_list = args[8],
                file_icon = args[9],
                findex = args[10],
                random = args[11])
        return result

    BT_TASK_COMMIT_URL = "http://dynamic.cloud.vip.xunlei.com/interface/bt_task_commit"
    def add_bt_task_with_dict(self, url, info):
        if info['flag'] == 0: return False
        data = dict(
                uid = self.uid,
                btname = info["title"],
                cid = info["cid"],
                goldbean = 0,
                silverbean = 0,
                tsize = info["size"],
                findex = "_".join([x for i, x in enumerate(info["findex"]) if info["valid_list"][i] == "1"]),
                size = "_".join([x for i, x in enumerate(info["size_list"]) if info["valid_list"][i] == "1"]),
                name = "undefined",
                o_taskid = 0,
                o_page = "task")
        data["from"] = 0
        r = self.session.post(self.BT_TASK_COMMIT_URL, data)
        if r.error:
            r.raise_for_status()
        DEBUG(pformat(r.content))
        if "top.location" in r.content:
            return True
        return False

    def add_bt_task(self, url, add_all=True):
        info = self.bt_task_check(url)
        if not info: return False
        if add_all:
            for i, v in enumerate(info["valid_list"]):
                if v == "0":
                    info["valid_list"][i] = "1"
        return self.add_bt_task_with_dict(info)

    TASK_CHECK_URL = "http://dynamic.cloud.vip.xunlei.com/interface/task_check?callback=queryCid&url=%(url)s&random=%(random)s&tcache=%(cachetime)d"
    def task_check(self, url):
        r = self.session.get(self.TASK_CHECK_URL % {
                                   "url": urllib.quote_plus(url),
                                   "random": self._random,
                                   "cachetime": self._now})
        if r.error:
            r.raise_for_status()
        #queryCid(cid,gcid,file_size,tname,goldbean_need,silverbean_need,is_full,random)
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        if len(args) < 8:
            return {}
        result = dict(
            cid = args[0],
            gcid = args[1],
            size = args[2],
            title = args[3],
            goldbean_need = args[4],
            silverbean_need = args[5],
            is_full = args[6],
            random = args[7])
        return result

    #TASK_COMMIT_URL = "http://dynamic.cloud.vip.xunlei.com/interface/task_commit?callback=ret_task&uid=%(uid)s&cid=%(cid)s&gcid=%(gcid)s&size=%(file_size)s&goldbean=%(goldbean_need)s&silverbean=%(silverbean_need)s&t=%(tname)s&url=%(url)s&type=%(task_type)s&o_page=task&o_taskid=0"
    TASK_COMMIT_URL = "http://dynamic.cloud.vip.xunlei.com/interface/task_commit"
    def add_task_with_dict(self, url, info):
        params = dict(
            callback="ret_task",
            uid=self.uid,
            cid=info['cid'],
            gcid=info['gcid'],
            size=info['size'],
            goldbean=0,
            silverbean=0,
            t=info['title'],
            url=url,
            type=0,
            o_page="task",
            o_taskid=0,)
        r = self.session.get(self.TASK_COMMIT_URL, params=params)
        if r.error:
            r.raise_for_status()
        DEBUG(pformat(r.content))
        if "top.location" in r.content:
            return True
        return False

    def add_task(self, url):
        info = self.task_check(url)
        if not info: return False
        return self.add_task_with_dict(info)

    BATCH_TASK_CHECK_URL = "http://dynamic.cloud.vip.xunlei.com/interface/batch_task_check"
    def batch_task_check(self, url_list):
        data = dict(url="\r\n".join(url_list), random=self._random)
        r = self.session.post(self.BATCH_TASK_CHECK_URL, data=data)
        if r.error:
            r.raise_for_status()
        m = re.search("""(parent.begin_task_batch_resp.*?)</script>""",
                      r.content)
        assert m
        function, args = parser_js_function_call(m.group(1))
        DEBUG(pformat(args))
        assert args
        return args[0] if args else {}

    BATCH_TASK_COMMIT_URL = "http://dynamic.cloud.vip.xunlei.com/interface/batch_task_commit"
    def add_batch_task_with_dict(self, url, info):
        data = dict(
                batch_old_taskid=",".join([0, ]*len(info)),
                )
        for i, task in enumerate(info):
            data["cid[%d]" % i] = task.get("cid", "")
            data["url[%d]" % i] = urllib.quote(task["url"])
        r = self.session.post(self.BATCH_TASK_COMMIT_URL, data=data)
        DEBUG(pformat(r.content))
        if r.error:
            r.raise_for_status()
        if "top.location" in r.content:
            return True
        return False

    def add_batch_task(self, url_list):
        # will failed of space limited
        info = self.batch_task_check(url_list)
        if not info: return False
        return self.add_batch_task_with_dict(info)

    FILL_BT_LIST = "http://dynamic.cloud.vip.xunlei.com/interface/fill_bt_list?callback=fill_bt_list&tid=%(tid)s&infoid=%(cid)s&g_net=1&p=1&uid=%(uid)s&noCacheIE=%(cachetime)d"
    def _get_bt_list(self, tid, cid):
        r = self.session.get(self.FILL_BT_LIST % dict(
                                tid = tid,
                                cid = cid,
                                uid = self.uid,
                                cachetime = self._now))
        if r.error:
            r.raise_for_status()
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        if not args:
            return {}
        if isinstance(args[0], basestring):
            raise LiXianAPIException, args[0]
        return args[0].get("Result", {})

    def get_bt_list(self, tid, cid):
        raw_data = self._get_bt_list(tid, cid)
        assert cid == raw_data.get("Infoid")
        result = []
        for r in raw_data.get("Record", []):
            tmp = dict(
                    task_id=int(r['taskid']),
                    url=r['url'],
                    lixian_url=r['downurl'],
                    cid=r['cid'],
                    title=r['title'],
                    status=self.d_status.get(int(r['download_status'])),
                    dirtitle=r['dirtitle'],
                    process=r['percent'],
                    size=int(r['filesize']),
                    format=r['openformat'],
                )
            result.append(tmp)
        return result

    TASK_DELAY_URL = "http://dynamic.cloud.vip.xunlei.com/interface/task_delay?taskids=%(ids)s&noCacheIE=%(cachetime)d"
    def delay_task(self, task_ids):
        tmp_ids = [str(x)+"_1" for x in task_ids]
        r = self.session.get(self.TASK_DELAY_URL % dict(
                            ids = ",".join(tmp_ids),
                            cachetime = self._now))
        if r.error:
            r.raise_for_status()
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        assert args
        if args and args[0].get("result") == 1:
            return True
        return False

    TASK_DELETE_URL = "http://dynamic.cloud.vip.xunlei.com/interface/task_delete?type=0&taskids=%(ids)s&noCacheIE=%(cachetime)d"
    def delete_task(self, task_ids):
        r = self.session.get(self.TASK_DELETE_URL % dict(
                            ids = urllib.quote_plus(",".join(task_ids)),
                            cachetime = self._now))
        if r.error:
            r.raise_for_status()
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        assert args
        if args and args[0].get("result") == 1:
            return True
        return False

    GET_WAIT_TIME_URL = "http://dynamic.cloud.vip.xunlei.com/interface/get_wait_time"
    def get_wait_time(self, task_id, key=None):
        params = dict(
            callback = "download_check_respo",
            t = self._now,
            taskid = task_id)
        if key:
            params["key"] = key
        r = self.session.get(self.GET_WAIT_TIME_URL, params=params)
        if r.error:
            r.raise_for_status()
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        assert args
        return args[0] if args else {}

    GET_FREE_URL = "http://dynamic.cloud.vip.xunlei.com/interface/free_get_url"
    def get_free_url(self, nm_list=[], bt_list=[]):
        #info = self.get_wait_time(task_id)
        #if info.get("result") != 0:
        #    return {}
        info = {}
        params = dict(
             key=info.get("key", ""),
             list=",".join((str(x) for x in nm_list+bt_list)),
             nm_list=",".join((str(x) for x in nm_list)),
             bt_list=",".join((str(x) for x in bt_list)),
             uid=self.uid,
             t=self._now)
        r = self.session.get(self.GET_FREE_URL, params=params)
        if r.error:
            r.raise_for_status()
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        assert args
        return args[0] if args else {}

    GET_TASK_PROCESS = "http://dynamic.cloud.vip.xunlei.com/interface/task_process"
    def get_task_process(self, nm_list=[], bt_list=[]):
        params = dict(
             callback="rebuild",
             list=",".join((str(x) for x in nm_list+bt_list)),
             nm_list=",".join((str(x) for x in nm_list)),
             bt_list=",".join((str(x) for x in bt_list)),
             uid=self.uid,
             noCacheIE=self._now,
             )
        r = self.session.get(self.GET_TASK_PROCESS, params=params)
        if r.error:
            r.raise_for_status()
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        assert args

        result = []
        for task in args[0].get("Process", {}).get("Record", []) if args else []:
            tmp = dict(
                    task_id = int(task['tid']),
                    cid = task.get('cid', None),
                    status = self.d_status.get(int(task['download_status']), "waiting"),
                    process = task['percent'],
                    leave_time = task['leave_time'],
                    speed = int(task['speed']),
                    lixian_url = task.get('lixian_url', None),
                  )
            result.append(tmp)
        return result

    SHARE_URL = "http://dynamic.sendfile.vip.xunlei.com/interface/lixian_forwarding"
    def share(self, emails, tasks, msg="", task_list=None):
        if task_list is None:
            task_list = self.get_task_list()
        payload = []
        i = 0
        for task in task_list:
            if task["task_id"] in tasks:
                if task["task_type"] == "bt":
                    #TODO
                    pass
                else:
                    if not task["lixian_url"]: continue
                    url_params = parse_url(task['lixian_url'])
                    tmp = {
                        "cid_%d" % i : task["cid"],
                        "file_size_%d" % i : task["size"],
                        "gcid_%d" % i : url_params.get("g", ""),
                        "url_%d" % i : task["url"],
                        "title_%d" % i : task["taskname"],
                        "section_%d" % i : url_params.get("scn", "")}
                    i += 1
                    payload.append(tmp)
        data = dict(
                uid = self.uid,
                sessionid = self.get_cookie("sessionid"),
                msg = msg,
                resv_email = ";".join(emails),
                data = json.dumps(payload))
        r = self.session.post(self.SHARE_URL, data)
        if r.error:
            r.raise_for_status()
        #forward_res(1,"ok",649513164808429);
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        assert args
        if args and args[0] == 1:
            return True
        return False

    CHECK_LOGIN_URL = "http://dynamic.cloud.vip.xunlei.com/interface/verify_login"
    TASK_URL = "http://dynamic.cloud.vip.xunlei.com/user_task?userid=%s"
    def check_login(self):
        r = self.session.get(self.CHECK_LOGIN_URL)
        if r.error:
            r.raise_for_status()
        function, args = parser_js_function_call(r.content)
        DEBUG(pformat(args))
        assert args
        if args and args[0].get("result") == 1:
            self.uid = int(args[0]["data"].get("userid"))
            self.isvip = args[0]["data"].get("vipstate")
            self.nickname = args[0]["data"].get("nickname")
            self.username = args[0]["data"].get("usrname")
            self.task_url = self.TASK_URL % self.uid
            return True
        return False

    def get_cookie(self, attr=""):
        cookies = requests.utils.dict_from_cookiejar(self.session.cookies)
        if attr:
            return cookies[attr]
        return cookies

    LOGOUT_URL = "http://login.xunlei.com/unregister?sessionid=%(sessionid)s"
    def logout(self):
        sessionid = self.get_cookie("sessionid")
        if sessionid:
            self.session.get(self.LOGOUT_URL % {"sessionid": sessionid})
        self.session.cookies.clear()
        self.islogin = False
        self.task_url = None
