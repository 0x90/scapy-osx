## This file is part of Scapy
## See http://www.secdev.org/projects/scapy for more informations
## Copyright (C) Philippe Biondi <phil@secdev.org>
## This program is published under a GPLv2 license

import types,itertools,time,os
from select import select
from collections import deque
import thread
from config import conf
from utils import do_graph
from error import log_interactive
from plist import PacketList
from data import MTU

class ObjectPipe:
    def __init__(self):
        self.rd,self.wr = os.pipe()
        self.queue = deque()
    def fileno(self):
        return self.rd
    def send(self, obj):
        self.queue.append(obj)
        os.write(self.wr,"X")
    def recv(self, n=0):
        os.read(self.rd,1)
        return self.queue.popleft()


##############
## Automata ##
##############

class ATMT:
    STATE = "State"
    ACTION = "Action"
    CONDITION = "Condition"
    RECV = "Receive condition"
    TIMEOUT = "Timeout condition"
    IOEVENT = "I/O event"

    class NewStateRequested(Exception):
        def __init__(self, state_func, automaton, *args, **kargs):
            self.func = state_func
            self.state = state_func.atmt_state
            self.initial = state_func.atmt_initial
            self.error = state_func.atmt_error
            self.final = state_func.atmt_final
            Exception.__init__(self, "Request state [%s]" % self.state)
            self.automaton = automaton
            self.args = args
            self.kargs = kargs
            self.action_parameters() # init action parameters
        def action_parameters(self, *args, **kargs):
            self.action_args = args
            self.action_kargs = kargs
            return self
        def run(self):
            return self.func(self.automaton, *self.args, **self.kargs)

    @staticmethod
    def state(initial=0,final=0,error=0):
        def deco(f,initial=initial, final=final):
            f.atmt_type = ATMT.STATE
            f.atmt_state = f.func_name
            f.atmt_initial = initial
            f.atmt_final = final
            f.atmt_error = error
            def state_wrapper(self, *args, **kargs):
                return ATMT.NewStateRequested(f, self, *args, **kargs)

            state_wrapper.func_name = "%s_wrapper" % f.func_name
            state_wrapper.atmt_type = ATMT.STATE
            state_wrapper.atmt_state = f.func_name
            state_wrapper.atmt_initial = initial
            state_wrapper.atmt_final = final
            state_wrapper.atmt_error = error
            state_wrapper.atmt_origfunc = f
            return state_wrapper
        return deco
    @staticmethod
    def action(cond, prio=0):
        def deco(f,cond=cond):
            if not hasattr(f,"atmt_type"):
                f.atmt_cond = {}
            f.atmt_type = ATMT.ACTION
            f.atmt_cond[cond.atmt_condname] = prio
            return f
        return deco
    @staticmethod
    def condition(state, prio=0):
        def deco(f, state=state):
            f.atmt_type = ATMT.CONDITION
            f.atmt_state = state.atmt_state
            f.atmt_condname = f.func_name
            f.atmt_prio = prio
            return f
        return deco
    @staticmethod
    def receive_condition(state, prio=0):
        def deco(f, state=state):
            f.atmt_type = ATMT.RECV
            f.atmt_state = state.atmt_state
            f.atmt_condname = f.func_name
            f.atmt_prio = prio
            return f
        return deco
    @staticmethod
    def ioevent(state, name, prio=0):
        def deco(f, state=state):
            f.atmt_type = ATMT.IOEVENT
            f.atmt_state = state.atmt_state
            f.atmt_condname = f.func_name
            f.atmt_ioname = name
            f.atmt_prio = prio
            return f
        return deco
    @staticmethod
    def timeout(state, timeout):
        def deco(f, state=state, timeout=timeout):
            f.atmt_type = ATMT.TIMEOUT
            f.atmt_state = state.atmt_state
            f.atmt_timeout = timeout
            f.atmt_condname = f.func_name
            return f
        return deco


class Automaton_metaclass(type):
    def __new__(cls, name, bases, dct):
        cls = super(Automaton_metaclass, cls).__new__(cls, name, bases, dct)
        cls.states={}
        cls.state = None
        cls.recv_conditions={}
        cls.conditions={}
        cls.ioevents={}
        cls.timeout={}
        cls.actions={}
        cls.initial_states=[]
        cls.ionames = []

        members = {}
        classes = [cls]
        while classes:
            c = classes.pop(0) # order is important to avoid breaking method overloading
            classes += list(c.__bases__)
            for k,v in c.__dict__.iteritems():
                if k not in members:
                    members[k] = v

        decorated = [v for v in members.itervalues()
                     if type(v) is types.FunctionType and hasattr(v, "atmt_type")]
        
        for m in decorated:
            if m.atmt_type == ATMT.STATE:
                s = m.atmt_state
                cls.states[s] = m
                cls.recv_conditions[s]=[]
                cls.ioevents[s]=[]
                cls.conditions[s]=[]
                cls.timeout[s]=[]
                if m.atmt_initial:
                    cls.initial_states.append(m)
            elif m.atmt_type in [ATMT.CONDITION, ATMT.RECV, ATMT.TIMEOUT, ATMT.IOEVENT]:
                cls.actions[m.atmt_condname] = []
    
        for m in decorated:
            if m.atmt_type == ATMT.CONDITION:
                cls.conditions[m.atmt_state].append(m)
            elif m.atmt_type == ATMT.RECV:
                cls.recv_conditions[m.atmt_state].append(m)
            elif m.atmt_type == ATMT.IOEVENT:
                cls.ioevents[m.atmt_state].append(m)
                cls.ionames.append(m.atmt_ioname)
            elif m.atmt_type == ATMT.TIMEOUT:
                cls.timeout[m.atmt_state].append((m.atmt_timeout, m))
            elif m.atmt_type == ATMT.ACTION:
                for c in m.atmt_cond:
                    cls.actions[c].append(m)
            

        for v in cls.timeout.itervalues():
            v.sort(lambda (t1,f1),(t2,f2): cmp(t1,t2))
            v.append((None, None))
        for v in itertools.chain(cls.conditions.itervalues(),
                                 cls.recv_conditions.itervalues(),
                                 cls.ioevents.itervalues()):
            v.sort(lambda c1,c2: cmp(c1.atmt_prio,c2.atmt_prio))
        for condname,actlst in cls.actions.iteritems():
            actlst.sort(lambda c1,c2: cmp(c1.atmt_cond[condname], c2.atmt_cond[condname]))

        return cls

        
    def graph(self, **kargs):
        s = 'digraph "%s" {\n'  % self.__class__.__name__
        
        se = "" # Keep initial nodes at the begining for better rendering
        for st in self.states.itervalues():
            if st.atmt_initial:
                se = ('\t"%s" [ style=filled, fillcolor=blue, shape=box, root=true];\n' % st.atmt_state)+se
            elif st.atmt_final:
                se += '\t"%s" [ style=filled, fillcolor=green, shape=octagon ];\n' % st.atmt_state
            elif st.atmt_error:
                se += '\t"%s" [ style=filled, fillcolor=red, shape=octagon ];\n' % st.atmt_state
        s += se

        for st in self.states.values():
            for n in st.atmt_origfunc.func_code.co_names+st.atmt_origfunc.func_code.co_consts:
                if n in self.states:
                    s += '\t"%s" -> "%s" [ color=green ];\n' % (st.atmt_state,n)
            

        for c,k,v in ([("purple",k,v) for k,v in self.conditions.items()]+
                      [("red",k,v) for k,v in self.recv_conditions.items()]+
                      [("orange",k,v) for k,v in self.ioevents.items()]):
            for f in v:
                for n in f.func_code.co_names+f.func_code.co_consts:
                    if n in self.states:
                        l = f.atmt_condname
                        for x in self.actions[f.atmt_condname]:
                            l += "\\l>[%s]" % x.func_name
                        s += '\t"%s" -> "%s" [label="%s", color=%s];\n' % (k,n,l,c)
        for k,v in self.timeout.iteritems():
            for t,f in v:
                if f is None:
                    continue
                for n in f.func_code.co_names+f.func_code.co_consts:
                    if n in self.states:
                        l = "%s/%.1fs" % (f.atmt_condname,t)                        
                        for x in self.actions[f.atmt_condname]:
                            l += "\\l>[%s]" % x.func_name
                        s += '\t"%s" -> "%s" [label="%s",color=blue];\n' % (k,n,l)
        s += "}\n"
        return do_graph(s, **kargs)
        


class Automaton:
    __metaclass__ = Automaton_metaclass

    class _IO:
        pass

    class _IO_wrapper:
        def __init__(self,rd,wr):
            self.rd = rd
            self.wr = wr
        def fileno(self):
            if type(self.rd) is int:
                return self.rd
            return self.rd.fileno()
        def recv(self, n=None):
            return self.rd.recv(n)
        def read(self, n=None):
            return self.rd.recv(n)        
        def send(self, msg):
            return self.wr.send(msg)
        def write(self, msg):
            return self.wr.send(msg)

            
    
    def __init__(self, *args, **kargs):
        self.running = False
        self.breakpointed = None
        self.breakpoints = set()
        self.debug_level=0
        self.init_args=args
        self.init_kargs=kargs
        self.io = self._IO()
        self.oi = self._IO()
        self.ioin = {}
        self.ioout = {}
        for n in self.ionames:
            self.ioin[n] = ioin = ObjectPipe()
            self.ioout[n] = ioout = ObjectPipe()
            ioin.ioname = n
            ioout.ioname = n
            setattr(self.io, n, self._IO_wrapper(ioout,ioin))
            setattr(self.oi, n, self._IO_wrapper(ioin,ioout))
        
        self.parse_args(*args, **kargs)

    def debug(self, lvl, msg):
        if self.debug_level >= lvl:
            log_interactive.debug(msg)
            



    class ErrorState(Exception):
        def __init__(self, msg, result=None):
            Exception.__init__(self, msg)
            self.result = result
    class Stuck(ErrorState):
        pass

    class Breakpoint(Exception):
        def __init__(self, msg, breakpoint):
            Exception.__init__(self, msg)
            self.breakpoint = breakpoint

    def parse_args(self, debug=0, store=1, **kargs):
        self.debug_level=debug
        self.socket_kargs = kargs
        self.store_packets = store
        

    def master_filter(self, pkt):
        return True

    def run_condition(self, cond, *args, **kargs):
        try:
            cond(self,*args, **kargs)
        except ATMT.NewStateRequested, state_req:
            self.debug(2, "%s [%s] taken to state [%s]" % (cond.atmt_type, cond.atmt_condname, state_req.state))
            if cond.atmt_type == ATMT.RECV:
                self.packets.append(args[0])
            for action in self.actions[cond.atmt_condname]:
                self.debug(2, "   + Running action [%s]" % action.func_name)
                action(self, *state_req.action_args, **state_req.action_kargs)
            raise
        else:
            self.debug(2, "%s [%s] not taken" % (cond.atmt_type, cond.atmt_condname))
            

    def add_breakpoints(self, *bps):
        for bp in bps:
            if hasattr(bp,"atmt_state"):
                bp = bp.atmt_state
            self.breakpoints.add(bp)

    def remove_breakpoints(self, *bps):
        for bp in bps:
            if hasattr(bp,"atmt_state"):
                bp = bp.atmt_state
            if bp in self.breakpoints:
                self.breakpoints.remove(pb)

    def start(self, *args, **kargs):
        self.running = True

        # Update default parameters
        a = args+self.init_args[len(args):]
        k = self.init_kargs
        k.update(kargs)
        self.parse_args(*a,**k)

        # Start the automaton
        self.state=self.initial_states[0](self)
        self.send_sock = conf.L3socket()
        self.listen_sock = conf.L2listen(**self.socket_kargs)
        self.packets = PacketList(name="session[%s]"%self.__class__.__name__)

    def next(self):
        if not self.running:
            self.start()
        try:
            self.debug(1, "## state=[%s]" % self.state.state)

            # Entering a new state. First, call new state function
            if self.state.state in self.breakpoints and self.state.state != self.breakpointed: 
                self.breakpointed = self.state.state
                raise self.Breakpoint("breakpoint triggered on state %s" % self.state.state,
                                      breakpoint = self.state.state)
            self.breakpointed = None
            state_output = self.state.run()
            if self.state.error:
                self.running = False
                raise self.ErrorState("Reached %s: [%r]" % (self.state.state, state_output), result=state_output)
            if self.state.final:
                self.running = False
                raise StopIteration(state_output)

            if state_output is None:
                state_output = ()
            elif type(state_output) is not list:
                state_output = state_output,
            
            # Then check immediate conditions
            for cond in self.conditions[self.state.state]:
                self.run_condition(cond, *state_output)

            # If still there and no conditions left, we are stuck!
            if ( len(self.recv_conditions[self.state.state]) == 0 and
                 len(self.ioevents[self.state.state]) == 0 and
                 len(self.timeout[self.state.state]) == 1 ):
                raise self.Stuck("stuck in [%s]" % self.state.state,result=state_output)

            # Finally listen and pay attention to timeouts
            expirations = iter(self.timeout[self.state.state])
            next_timeout,timeout_func = expirations.next()
            t0 = time.time()
            
            fds = []
            if len(self.recv_conditions[self.state.state]) > 0:
                fds.append(self.listen_sock)
            for ioev in self.ioevents[self.state.state]:
                fds.append(self.ioin[ioev.atmt_ioname])
            while 1:
                t = time.time()-t0
                if next_timeout is not None:
                    if next_timeout <= t:
                        self.run_condition(timeout_func, *state_output)
                        next_timeout,timeout_func = expirations.next()
                if next_timeout is None:
                    remain = None
                else:
                    remain = next_timeout-t

                r,_,_ = select(fds,[],[],remain)
                for fd in r:
                    if fd == self.listen_sock:
                        pkt = self.listen_sock.recv(MTU)
                        if pkt is not None:
                            if self.master_filter(pkt):
                                self.debug(3, "RECVD: %s" % pkt.summary())
                                for rcvcond in self.recv_conditions[self.state.state]:
                                    self.run_condition(rcvcond, pkt, *state_output)
                            else:
                                self.debug(4, "FILTR: %s" % pkt.summary())
                    else:
                        self.debug(3, "IOEVENT on %s" % fd.ioname)
                        for ioevt in self.ioevents[self.state.state]:
                            if ioevt.atmt_ioname == fd.ioname:
                                self.run_condition(ioevt, fd, *state_output)

        except ATMT.NewStateRequested,state_req:
            self.debug(2, "switching from [%s] to [%s]" % (self.state.state,state_req.state))
            self.state = state_req
            return state_req
        


    def run(self, *args, **kargs):
        if not self.running:
            self.start(*args, **kargs)

        while 1:
            try:
                self.next()
            except KeyboardInterrupt:
                self.debug(1,"Interrupted by user")
                break
            except StopIteration,e:
                return e.args[0]

    cont = run

    def run_bg(self, *args, **kargs):
        self.threadid = thread.start_new_thread(self.run, args, kargs)
        
    def __iter__(self):
        if not self.running:
            self.start()
        return self

    def my_send(self, pkt):
        self.send_sock.send(pkt)

    def send(self, pkt):
        self.my_send(pkt)
        self.debug(3,"SENT : %s" % pkt.summary())
        self.packets.append(pkt.copy())

