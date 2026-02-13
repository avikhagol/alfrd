from collections import deque
import time, datetime, inspect
from alfrd import __version__
import traceback

class LiveLog:
    def __init__(self, max_logs=1000):
        self.buffer = deque(maxlen=max_logs)
        
        self.buffer.append(f"ALFRD ({__version__}) - Log started")
    
    def log(self, msg, level="INFO"):
        timestamp           =   datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        
        calledby            =   inspect.stack()[1]
        callerself          =   calledby.frame.f_locals.get('self', None)
        caller_classname    =   callerself.__class__.__name__ if callerself else "Global"
        fnname              =   calledby.function
        svc                 =   f"{caller_classname}::{fnname}"
        logque              =   f"{timestamp} {level} {svc}:: {msg}"
        self.buffer.append(logque)
        if level=="FAIL":
            for err in traceback.format_exc().splitlines():
                if err:
                    self.buffer.append(err)
        

livelogger = LiveLog()