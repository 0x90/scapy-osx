from scapy.data import KnowledgeBase
from scapy.config import conf

##########################
## IP location database ##
##########################

class IPCountryKnowledgeBase(KnowledgeBase):
    """
How to generate the base :
db = []
for l in open("GeoIPCountryWhois.csv").readlines():
    s,e,c = l.split(",")[2:5]
    db.append((int(s[1:-1]),int(e[1:-1]),c[1:-1]))
cPickle.dump(gzip.open("xxx","w"),db)
"""
    def lazy_init(self):
        self.base = load_object(self.filename)


class CountryLocKnowledgeBase(KnowledgeBase):
    def lazy_init(self):
        f=open(self.filename)
        self.base = {}
        while 1:
            l = f.readline()
            if not l:
                break
            l = l.strip().split(",")
            if len(l) != 3:
                continue
            c,lat,long = l
            
            self.base[c] = (float(long),float(lat))
        f.close()
            
        


def locate_ip(ip):
    ip=map(int,ip.split("."))
    ip = ip[3]+(ip[2]<<8L)+(ip[1]<<16L)+(ip[0]<<24L)

    cloc = country_loc_kdb.get_base()
    db = IP_country_kdb.get_base()

    d=0
    f=len(db)-1
    while (f-d) > 1:
        guess = (d+f)/2
        if ip > db[guess][0]:
            d = guess
        else:
            f = guess
    s,e,c = db[guess]
    if  s <= ip and ip <= e:
        return cloc.get(c,None)





conf.IP_country_kdb = IPCountryKnowledgeBase(conf.IPCountry_base)
conf.country_loc_kdb = CountryLocKnowledgeBase(conf.countryLoc_base)
