## This file is part of Scapy
## See http://www.secdev.org/projects/scapy for more informations
## Copyright (C) Philippe Biondi <phil@secdev.org>
## This program is published under a GPLv2 license


from __future__ import with_statement
import sys,os,struct,socket,time
from fcntl import ioctl
import scapy.utils
from scapy.config import conf
from scapy.data import *
from scapy.supersocket import SuperSocket



# From bits/ioctls.h
SIOCGIFHWADDR  = 0x8927          # Get hardware address    
SIOCGIFADDR    = 0x8915          # get PA address          
SIOCGIFNETMASK = 0x891b          # get network PA mask     
SIOCGIFNAME    = 0x8910          # get iface name          
SIOCSIFLINK    = 0x8911          # set iface channel       
SIOCGIFCONF    = 0x8912          # get iface list          
SIOCGIFFLAGS   = 0x8913          # get flags               
SIOCSIFFLAGS   = 0x8914          # set flags               
SIOCGIFINDEX   = 0x8933          # name -> if_index mapping
SIOCGIFCOUNT   = 0x8938          # get number of devices
SIOCGSTAMP     = 0x8906          # get packet timestamp (as a timeval)

# From if.h
IFF_UP = 0x1               # Interface is up.
IFF_BROADCAST = 0x2        # Broadcast address valid.
IFF_DEBUG = 0x4            # Turn on debugging.
IFF_LOOPBACK = 0x8         # Is a loopback net.
IFF_POINTOPOINT = 0x10     # Interface is point-to-point link.
IFF_NOTRAILERS = 0x20      # Avoid use of trailers.
IFF_RUNNING = 0x40         # Resources allocated.
IFF_NOARP = 0x80           # No address resolution protocol.
IFF_PROMISC = 0x100        # Receive all packets.

# From netpacket/packet.h
PACKET_ADD_MEMBERSHIP  = 1
PACKET_DROP_MEMBERSHIP = 2
PACKET_RECV_OUTPUT     = 3
PACKET_RX_RING         = 5
PACKET_STATISTICS      = 6
PACKET_MR_MULTICAST    = 0
PACKET_MR_PROMISC      = 1
PACKET_MR_ALLMULTI     = 2

# From bits/socket.h
SOL_PACKET = 263
# From asm/socket.h
SO_ATTACH_FILTER = 26
SOL_SOCKET = 1

# From net/route.h
RTF_UP = 0x0001  # Route usable
RTF_REJECT = 0x0200



LOOPBACK_NAME="lo"

with os.popen("tcpdump -V 2> /dev/null") as f:
    if f.close() >> 8 == 0x7f:
        log_loading.warning("Failed to execute tcpdump. Check it is installed and in the PATH")
        TCPDUMP=0
    else:
        TCPDUMP=1
        
    

def get_if_raw_hwaddr(iff):
    return struct.unpack("16xh6s8x",get_if(iff,SIOCGIFHWADDR))

def get_if_raw_addr(iff):
    try:
        return get_if(iff, SIOCGIFADDR)[20:24]
    except IOError:
        return "\0\0\0\0"


def get_if_list():
    f=open("/proc/net/dev","r")
    lst = []
    f.readline()
    f.readline()
    for l in f:
        lst.append(l.split(":")[0].strip())
    return lst
def get_working_if():
    for i in get_if_list():
        if i == LOOPBACK_NAME:                
            continue
        ifflags = struct.unpack("16xH14x",get_if(i,SIOCGIFFLAGS))[0]
        if ifflags & IFF_UP:
            return i
    return LOOPBACK_NAME
def attach_filter(s, filter):
    # XXX We generate the filter on the interface conf.iface 
    # because tcpdump open the "any" interface and ppp interfaces
    # in cooked mode. As we use them in raw mode, the filter will not
    # work... one solution could be to use "any" interface and translate
    # the filter from cooked mode to raw mode
    # mode
    if not TCPDUMP:
        return
    try:
        f = os.popen("%s -i %s -ddd -s 1600 '%s'" % (config.conf.prog.tcpdump,config.conf.iface,filter))
    except OSError,msg:
        log_interactive.warning("Failed to execute tcpdump: (%s)")
        return
    lines = f.readlines()
    if f.close():
        raise Scapy_Exception("Filter parse error")
    nb = int(lines[0])
    bpf = ""
    for l in lines[1:]:
        bpf += struct.pack("HBBI",*map(long,l.split()))

    # XXX. Argl! We need to give the kernel a pointer on the BPF,
    # python object header seems to be 20 bytes. 36 bytes for x86 64bits arch.
    if X86_64:
        bpfh = struct.pack("HL", nb, id(bpf)+36)
    else:
        bpfh = struct.pack("HI", nb, id(bpf)+20)  
    s.setsockopt(SOL_SOCKET, SO_ATTACH_FILTER, bpfh)

def set_promisc(s,iff,val=1):
    mreq = struct.pack("IHH8s", get_if_index(iff), PACKET_MR_PROMISC, 0, "")
    if val:
        cmd = PACKET_ADD_MEMBERSHIP
    else:
        cmd = PACKET_DROP_MEMBERSHIP
    s.setsockopt(SOL_PACKET, cmd, mreq)



def read_routes():
    f=open("/proc/net/route","r")
    routes = []
    s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ifreq = ioctl(s, SIOCGIFADDR,struct.pack("16s16x",LOOPBACK_NAME))
    addrfamily = struct.unpack("h",ifreq[16:18])[0]
    if addrfamily == socket.AF_INET:
        ifreq2 = ioctl(s, SIOCGIFNETMASK,struct.pack("16s16x",LOOPBACK_NAME))
        msk = socket.ntohl(struct.unpack("I",ifreq2[20:24])[0])
        dst = socket.ntohl(struct.unpack("I",ifreq[20:24])[0]) & msk
        ifaddr = scapy.utils.inet_ntoa(ifreq[20:24])
        routes.append((dst, msk, "0.0.0.0", LOOPBACK_NAME, ifaddr))
    else:
        warning("Interface lo: unkown address family (%i)"% addrfamily)

    for l in f.readlines()[1:]:
        iff,dst,gw,flags,x,x,x,msk,x,x,x = l.split()
        flags = int(flags,16)
        if flags & RTF_UP == 0:
            continue
        if flags & RTF_REJECT:
            continue
        try:
            ifreq = ioctl(s, SIOCGIFADDR,struct.pack("16s16x",iff))
        except IOError: # interface is present in routing tables but does not have any assigned IP
            ifaddr="0.0.0.0"
        else:
            addrfamily = struct.unpack("h",ifreq[16:18])[0]
            if addrfamily == socket.AF_INET:
                ifaddr = scapy.utils.inet_ntoa(ifreq[20:24])
            else:
                warning("Interface %s: unkown address family (%i)"%(iff, addrfamily))
                continue
        routes.append((socket.htonl(long(dst,16))&0xffffffffL,
                       socket.htonl(long(msk,16))&0xffffffffL,
                       scapy.utils.inet_ntoa(struct.pack("I",long(gw,16))),
                       iff, ifaddr))
    
    f.close()
    return routes

def get_if(iff,cmd):
    s=socket.socket()
    ifreq = ioctl(s, cmd, struct.pack("16s16x",iff))
    s.close()
    return ifreq


def get_if_index(iff):
    return int(struct.unpack("I",get_if(iff, SIOCGIFINDEX)[16:20])[0])

def get_last_packet_timestamp(sock):
    ts = ioctl(sock, SIOCGSTAMP, "12345678")
    s,us = struct.unpack("II",ts)
    return s+us/1000000.0



def _flush_fd(fd):
    if type(fd) is not int:
        fd = fd.fileno()
    while 1:
        r,w,e = select([fd],[],[],0)
        if r:
            os.read(fd,MTU)
        else:
            break





class L3PacketSocket(SuperSocket):
    def __init__(self, type = ETH_P_ALL, filter=None, promisc=None, iface=None, nofilter=0):
        self.type = type
        self.ins = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(type))
        self.ins.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 0)
        _flush_fd(self.ins)
        if iface:
            self.ins.bind((iface, type))
        if not nofilter:
            if conf.except_filter:
                if filter:
                    filter = "(%s) and not (%s)" % (filter, conf.except_filter)
                else:
                    filter = "not (%s)" % conf.except_filter
            if filter is not None:
                arch.attach_filter(self.ins, filter)
        self.ins.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**30)
        self.outs = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(type))
        self.outs.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2**30)
        if promisc is None:
            promisc = conf.promisc
        self.promisc = promisc
        if self.promisc:
            if iface is None:
                self.iff = arch.get_if_list()
            else:
                if iface.__class__ is list:
                    self.iff = iface
                else:
                    self.iff = [iface]
            for i in self.iff:
                arch.set_promisc(self.ins, i)
    def close(self):
        if self.closed:
            return
        self.closed=1
        if self.promisc:
            for i in self.iff:
                arch.set_promisc(self.ins, i, 0)
        SuperSocket.close(self)
    def recv(self, x):
        pkt, sa_ll = self.ins.recvfrom(x)
        if sa_ll[2] == socket.PACKET_OUTGOING:
            return None
        if sa_ll[3] in conf.l2types:
            cls = conf.l2types[sa_ll[3]]
            lvl = 2
        elif sa_ll[1] in conf.l3types:
            cls = conf.l3types[sa_ll[1]]
            lvl = 3
        else:
            warning("Unable to guess type (interface=%s protocol=%#x family=%i). Using Ethernet" % (sa_ll[0],sa_ll[1],sa_ll[3]))
            cls = Ether
            lvl = 2

        try:
            pkt = cls(pkt)
        except KeyboardInterrupt:
            raise
        except:
            if conf.debug_dissector:
                raise
            pkt = Raw(pkt)
        if lvl == 2:
            pkt = pkt.payload
            
        if pkt is not None:
            pkt.time = arch.get_last_packet_timestamp(self.ins)
        return pkt
    
    def send(self, x):
        iff,a,gw  = x.route()
        if iff is None:
            iff = conf.iface
        sdto = (iff, self.type)
        self.outs.bind(sdto)
        sn = self.outs.getsockname()
        ll = lambda x:x
        if sn[3] in (ARPHDR_PPP,ARPHDR_TUN):
            sdto = (iff, ETH_P_IP)
        if sn[3] in conf.l2types:
            ll = lambda x:conf.l2types[sn[3]]()/x
        try:
            sx = str(ll(x))
            x.sent_time = time.time()
            self.outs.sendto(sx, sdto)
        except socket.error,msg:
            x.sent_time = time.time()  # bad approximation
            if conf.auto_fragment and msg[0] == 90:
                for p in fragment(x):
                    self.outs.sendto(str(ll(p)), sdto)
            else:
                raise
                    



class L2Socket(SuperSocket):
    def __init__(self, iface = None, type = ETH_P_ALL, filter=None, nofilter=0):
        if iface is None:
            iface = conf.iface
        self.ins = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(type))
        self.ins.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 0)
        _flush_fd(self.ins)
        if not nofilter: 
            if conf.except_filter:
                if filter:
                    filter = "(%s) and not (%s)" % (filter, conf.except_filter)
                else:
                    filter = "not (%s)" % conf.except_filter
            if filter is not None:
                arch.attach_filter(self.ins, filter)
        self.ins.bind((iface, type))
        self.ins.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**30)
        self.outs = self.ins
        self.outs.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2**30)
        sa_ll = self.outs.getsockname()
        if sa_ll[3] in conf.l2types:
            self.LL = conf.l2types[sa_ll[3]]
        elif sa_ll[1] in conf.l3types:
            self.LL = conf.l3types[sa_ll[1]]
        else:
            warning("Unable to guess type (interface=%s protocol=%#x family=%i). Using Ethernet" % (sa_ll[0],sa_ll[1],sa_ll[3]))
            self.LL = Ether
            
    def recv(self, x):
        pkt, sa_ll = self.ins.recvfrom(x)
        if sa_ll[2] == socket.PACKET_OUTGOING:
            return None
        try:
            q = self.LL(pkt)
        except KeyboardInterrupt:
            raise
        except:
            if conf.debug_dissector:
                raise
            q = Raw(pkt)
        q.time = get_last_packet_timestamp(self.ins)
        return q


class L2ListenSocket(SuperSocket):
    def __init__(self, iface = None, type = ETH_P_ALL, promisc=None, filter=None, nofilter=0):
        self.type = type
        self.outs = None
        self.ins = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(type))
        self.ins.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 0)
        _flush_fd(self.ins)
        if iface is not None:
            self.ins.bind((iface, type))
        if not nofilter:
            if conf.except_filter:
                if filter:
                    filter = "(%s) and not (%s)" % (filter, conf.except_filter)
                else:
                    filter = "not (%s)" % conf.except_filter
            if filter is not None:
                arch.attach_filter(self.ins, filter)
        if promisc is None:
            promisc = conf.sniff_promisc
        self.promisc = promisc
        if iface is None:
            self.iff = arch.get_if_list()
        else:
            if iface.__class__ is list:
                self.iff = iface
            else:
                self.iff = [iface]
        if self.promisc:
            for i in self.iff:
                arch.set_promisc(self.ins, i)
        self.ins.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2**30)
    def close(self):
        if self.promisc:
            for i in self.iff:
                arch.set_promisc(self.ins, i, 0)
        SuperSocket.close(self)

    def recv(self, x):
        pkt, sa_ll = self.ins.recvfrom(x)
        if sa_ll[3] in conf.l2types :
            cls = conf.l2types[sa_ll[3]]
        elif sa_ll[1] in conf.l3types:
            cls = conf.l3types[sa_ll[1]]
        else:
            warning("Unable to guess type (interface=%s protocol=%#x family=%i). Using Ethernet" % (sa_ll[0],sa_ll[1],sa_ll[3]))
            cls = Ether

        try:
            pkt = cls(pkt)
        except KeyboardInterrupt:
            raise
        except:
            if conf.debug_dissector:
                raise
            pkt = Raw(pkt)
        pkt.time = get_last_packet_timestamp(self.ins)
        return pkt
    
    def send(self, x):
        raise Scapy_Exception("Can't send anything with L2ListenSocket")


conf.L3socket = L3PacketSocket
conf.L2socket = L2Socket
conf.L2listen = L2ListenSocket

conf.iface = get_working_if()
