from sqlalchemy import *
from sqlalchemy.orm import *
from sqlalchemy.exceptions import SQLError
from sqlalchemy import *
import sqlalchemy.types as sqltypes
import ConfigParser

import datetime

from ipblocker.config import config

import psycopg2
import random

def connect():
    db_config = dict(config.items('db'))
    hosts = db_config.pop('host').split(',')
    random.shuffle(hosts)
    for host in hosts:
        try :
            return psycopg2.connect(host=host, user=db_config['user'], password=db_config['password'], database=db_config['database'])
        except Exception, e:
            pass
    raise e

engine = create_engine('postgres://', creator=connect,pool_recycle=60)
Session = scoped_session(sessionmaker(autoflush=True, transactional=False, bind=engine))
metadata = MetaData(bind=engine)
mapper = Session.mapper

from webhelpers import distance_of_time_in_words

class PGMac(sqltypes.TypeEngine):
    def get_col_spec(self):
        return "MACADDR"

class PGInet(sqltypes.TypeEngine):
    def get_col_spec(self):
        return "INET"

class DontBlockException(Exception):
    def __init__(self, ip, who, comment):
        self.ip = ip
        self.who = who
        self.comment = comment
    def __str__(self):
        return "Dont Block Exception for %s (%s: %s)" % (
            self.ip, self.who, self.comment)

blocks = Table('blocks', metadata,
    Column('id',        Integer, primary_key=True),
    Column('ip',        PGInet, index=True),
    Column('who',       String(50), nullable=False),
    Column('comment',   String, nullable=False),
    Column('entered',   DateTime, default=datetime.datetime.now),
    Column('blocked',   DateTime, index=True),
    Column('unblocked', DateTime, index=True),
    Column('unblock_at', DateTime, nullable=False),
    Column('unblock_now',   Boolean, default=False,nullable=False)
)

dont_block = Table('dont_block', metadata,
    Column('id',        Integer, primary_key=True),
    Column('ip',        PGInet, index=True),
    Column('who',       String(50), nullable=False),
    Column('comment',   String, nullable=False),
    Column('entered',   DateTime, default=datetime.datetime.now),
)

#and IP can be pending blocked, but then set unblock_now
#at this point, blocked=NULL, unblocked=NULL
#the manager should try and unblock it(a noop) and then set
#unblocked=now(), so blocked will still be NULL
class Block(object):
    def __repr__(self):
        return 'Block(ip="%s")' % self.ip

    def set_blocked(self):
        """Set this IP from pending block -> blocked"""
        if self.blocked:
            raise Exception("%s Already blocked!" % self.ip)
        if self.unblocked:
            raise Exception("%s Already unblocked!" % self.ip)
        self.blocked = datetime.datetime.now()
        Session.flush()

    def set_unblocked(self):
        """Set this IP from blocked -> unblocked"""
        if self.unblocked: 
            raise Exception("%s Already unblocked!" % self.ip)
        #if not self.blocked:
        #    raise Exception("%s not blocked yet!!" % self.ip)
        self.unblocked = datetime.datetime.now()
        Session.flush()

    def set_unblock_now(self):
        """Set this IP to be unblocked"""
        self.unblock_now = True
        Session.flush()

    def _get_unblock_at_relative(self):
        now = datetime.datetime.now()
        diff = self.unblock_at - now
        ago = ''
        if now > self.unblock_at:
            ago=' ago'
        return distance_of_time_in_words(now, self.unblock_at) + ago
    unblock_at_relative = property(_get_unblock_at_relative)

    def _get_unblock_pending(self):
        now = datetime.datetime.now()
        return  now > self.unblock_at
    unblock_pending = property(_get_unblock_pending)

class DontBlock(object):
    def __repr__(self):
        return 'DontBlock(ip="%s")' % self.ip

def get_all():
    """Return a list of all the blocked and pending IPS"""
    return Block.query.filter(Block.unblocked==None).all()

def get_all_that_should_be_blocked():
    """Return a list of all the blocked and pending IPS"""
    return Block.query.filter(and_(
            Block.unblocked==None,            #hasn't been unblocked yet
            Block.unblock_now == False,       #it isn't forced unblocked
            func.now() < Block.unblock_at,    #it hasn't timed out yet
            ~exists([1],dont_block.c.ip.op(">>=")(Block.ip)),
            )).all()

def get_blocked():
    """Return a list of the currently blocked IPS"""
    return Block.query.filter(and_(Block.blocked!=None,Block.unblocked==None)).all()

def get_blocked_ip(ip):
    """Return a single Blocked IP, or None if it is not currently blocked
       Pending is considered Blocked, otherwise unblock_now won't work right"""
    return Block.query.filter(and_(Block.unblocked==None,Block.ip==ip)).first()

def get_block_pending():
    """Return a list of the IPS that are pending being blocked
       Also checks the dont_block table for any addresses that were set to be not blocked
       after an address was blocked
    """
    return Block.query.filter(and_(
        Block.blocked==None, #it's not already blocked
        Block.unblock_now==False, #it isn't forced unblocked
        func.now() < Block.unblock_at,    #it hasn't already timed out
        ~exists([1],dont_block.c.ip.op(">>=")(Block.ip)),
        )).all()

def get_unblock_pending():
    """Return a list of the IPS that are pending being un-blocked
       Also checks the dont_block table for any addresses that were set to be not blocked
       after an address was blocked
    """
    return Block.query.filter(and_(Block.unblocked==None,
            or_(
                Block.unblock_now==True,                   #force unblock
                func.now() > Block.unblock_at,             #block expired
                exists([1],dont_block.c.ip.op(">>=")(Block.ip)) #ip shouldn't be blocked
            ))).all()

def list_dont_block_records():
    """Return the list of don't block records"""
    return DontBlock.query.all()

def add_dont_block_record(ip, who, comment):
    b = DontBlock(ip=ip, who=who, comment=comment)
    Session.flush()
    return b

def get_dont_block_record(ip):
    """Return a record from the dont_block table for this ip, if one exists"""
    r = DontBlock.query.filter(dont_block.c.ip.op(">>=")(ip)).all()
    if r:
        return r[0]

def delete_dont_block_record(id):
    dont_block.delete(dont_block.c.id==id).execute().close()

def ok_to_block(ip):
    """Is this IP ok to block?"""
    r = get_dont_block_record(ip)
    return not r


def block_ip(ip, who, comment, duration):
    """Block this IP address"""

    ex = get_dont_block_record(ip)
    if ex:
        raise DontBlockException(ex.ip, ex.who, ex.comment)

    now = datetime.datetime.now()
    diff = datetime.timedelta(seconds=duration)
    unblock_at = now + diff

    b = get_blocked_ip(ip)
    if b:
        b.who = who
        b.comment = comment
        b.unblock_at = unblock_at
    else:
        b = Block(ip=ip, who=who, comment=comment, unblock_at=unblock_at)
    Session.flush()
    return b

def unblock_ip(ip):
    """Un-block this IP address"""
    b = get_blocked_ip(ip)
    if b:
        b.set_unblock_now()
        return True
    else:
        return False

mapper(Block, blocks)
mapper(DontBlock, dont_block)

#CREATE INDEX idx_blocked_null   ON blocks (blocked)   WHERE blocked IS NULL;
#CREATE INDEX idx_unblocked_null ON blocks (unblocked) WHERE unblocked IS NOT NULL;

__all__ = '''
    Block
    get_blocked get_blocked_ip get_block_pending get_unblock_pending
    get_dont_block_record
    block_ip unblock_ip'''.split()
