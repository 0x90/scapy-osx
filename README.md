# scapy-osx

Latest Scapy version patched for OSX. Tested at 10.10.3

## Installation

Update ports tree and install dependencies
```
port selfupdate
port install libdnet py27-libdnet py-readline py-gnuplot py-crypto py-pyx swig gnuplot graphviz
```

Then install with from source pip
```
pip install -e "git+https://github.com/0x90/pylibpcap-osx#egg=pylibpcap-osx"
pip install -e "git+https://github.com/0x90/scapy-osx#egg=scapy"
```
For virtualenv install libdnet manually! 
