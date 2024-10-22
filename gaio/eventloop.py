import heapq 
from collections import deque
import time
from enum import Enum
from reactors import BaseSelectReactor, Reactor
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import os
import socket

_MAX_THREAD_COUNT  = os.cpu_count() ** 2
_MAX_PROCESS_COUNT = os.cpu_count()

class Awaitable:
    def __await__(self):
        yield


def kernel_switch():
    return Awaitable()


class State(Enum):
    PENDING = 'PENDING'
    CANCELLED = 'CANCELLED'
    FINISHED = 'FINISHED'

class Event:

    def __init__(self):
        self.__setter = False
        self.__coro_registry = deque()
        self.__state = State.PENDING
        self._loop = get_event_loop()
        
    def done(self) -> bool:
        return self.__setter

    def __await__(self):
        
        if self.__state == State.PENDING or self.__setter == False:
            # wait until state goes FINISHED

            self.__coro_registry.append(self._loop.current)
            self._loop._EventLoop__current = None 
            
            yield self

    async def wait(self):
        
        if self.__state == State.PENDING or self.__setter == False:
            # wait until state goes FINISHED

            self.__coro_registry.append(self._loop.current)
            self._loop._EventLoop__current = None
            await kernel_switch()

    def signal(self):
        
        """
            Signal the event for completion of waiting task
        """

        self.__setter = True
        self.__state = State.FINISHED

        while self.__coro_registry:
            self._loop.call_soon(self.__coro_registry.popleft())

class Task:

    def __init__(self, coro):
        self.__coro = coro
        self.__value = None 
        self.__event = Event()
    
    def __await__(self):
        if self.__event.done() is False:
            yield from self.__event.__await__()
        
        return self.__value 

    def __call__(self):
        
        try:
            self.__event._loop._EventLoop__current = self
            self.__coro.send(None)

            if self.__event._loop.current is not None:
                self.__event._loop.call_soon(self)

        except StopIteration as e:
            self.__value = e.value
            self.__event.signal()


class EventLoop:

    __instance = None 
    def __init__(self):
        self.__readyTask = deque()
        self.__sleepingTask = [  ]
        self.__current = None 
        
        self.__id = 0
        
        """ SOCKET IO POLLING REACTOR  """
       # self._io_reactor = BaseSelectReactor(self)
        self._io_reactor = Reactor(self)


        """  Used for offloading the tasks to the external thread or process so that operating system can schedule them 
             to the different processot at multicore allowing other task to run in eventloop making eventloop non blocking
             
             !!! Limited number of tasks can be offloaded depending upon the thread and the process of the 
                Computer
        """

        self._ThreadPool = ThreadPoolExecutor(max_workers=_MAX_THREAD_COUNT)
        self._ProcessPool = ProcessPoolExecutor(max_workers=_MAX_PROCESS_COUNT)


    @classmethod
    def get_instace(cls):
        if cls.__instance is None:
            cls.__instance = cls()

        return cls.__instance
    
    @property
    def current(self):
        return self.__current
    
    def call_soon(self, task):
        if not isinstance(task, Task):
            task = Task(task)

        self.__readyTask.append(task)
        return task
    
    """ Only tasks are allowed to make the use of this function  """
    def call_later(self, delay, task):
        self.__id += 1
        deadline = delay + time.time()
        heapq.heappush(self.__sleepingTask, (deadline, self.__id,task))
   

    """ !!!! Only for socket io  """ 
    def read_wait(self, fileno, task):
        self._io_reactor.register_readers(fileno, task)

    def write_wait(self, fileno, task):
        self._io_reactor.register_writers(fileno, task)

    def run_policy(self):

        while any([self.__readyTask, self.__sleepingTask, self._io_reactor]):

            if not self.__readyTask:
                
                if self.__sleepingTask:
                    deadline, _, task = self.__sleepingTask[0]
                    timeout = deadline - time.time()

                    if timeout < 0:
                        timeout = 0
                
                else:
                    timeout = None 
                
                # calling reactor to poll the file descriptor related to socket  
                self._io_reactor.poll(timeout)

                # now checkint if the task that are awaken from sleep are ready to execuite in a non blocking task queue
                now = time.time()
                while self.__sleepingTask:
                    if now >= self.__sleepingTask[0][0]:
                        self.__readyTask.append(heapq.heappop(self.__sleepingTask)[2])
                    else:
                        break

            self.__current = self.__readyTask.popleft()

            self.__current()

def get_event_loop() -> EventLoop:
    return EventLoop.get_instace()

def spawn(task):
    loop = get_event_loop()
    return loop.call_soon(task)

def start(task):
    loop = get_event_loop()
    loop.call_soon(task)
    loop.run_policy()


async def sleep(delay):
    loop = get_event_loop()
    loop.call_later(delay,loop.current)
    loop._EventLoop__current = None 
    await kernel_switch()


async def __socket_ready(fut,prom, sock_event):
    sock_event.recv(10)
    prom.set_value(fut.result())
    sock_event.close()

def run_and_notify(func, *args, notify_sock_peer):
    try:
        result = func(*args)
    finally:
        notify_sock_peer.send(b'x')
        return result


def run_in_thread(func, *args):
    loop = get_event_loop()
    future_notify, future_event = socket.socketpair()
    fut = loop._ThreadPool.submit(run_and_notify, func,*args, notify_sock_peer=future_notify)

    p = Promise()
    loop.read_wait(future_event, __socket_ready(fut, p, future_event))
    
    return p

def run_in_process(func, *args):
    loop = get_event_loop()
    future_notify, future_event = socket.socketpair()
    fut = loop._ProcessPool.submit(run_and_notify, func,*args, notify_sock_peer=future_notify)

    p = Promise()
    loop.read_wait(future_event, __socket_ready(fut, p, future_event))

    return p



""" Importing the Promise downward so that we can make it avoid circular dependency  """
from promise import Promise

