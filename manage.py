"""Local administrator operations. Passwords are entered securely, never as CLI arguments."""
import argparse
import getpass
import json
import re
import sqlite3
from pathlib import Path
from app import server

def main():
    parser=argparse.ArgumentParser(description='Lumrivo Clinical demonstration administration')
    sub=parser.add_subparsers(dest='command',required=True)
    hospital=sub.add_parser('create-hospital',help='Create an isolated fictional hospital and its administrator')
    hospital.add_argument('--name',required=True);hospital.add_argument('--admin-email',required=True)
    backup=sub.add_parser('backup',help='Create a consistent SQLite backup')
    backup.add_argument('destination',type=Path)
    inspect=sub.add_parser('check-backup',help='Read-only integrity check')
    inspect.add_argument('file',type=Path)
    args=parser.parse_args()
    if args.command=='create-hospital':
        email=args.admin_email.strip().lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):parser.error('Use a valid administrator email.')
        pw=getpass.getpass('Initial administrator password (16+ characters): ')
        if len(pw)<16:parser.error('Password must have at least 16 characters.')
        if pw!=getpass.getpass('Confirm password: '):parser.error('Passwords do not match.')
        org=server.uid('hospital');user=server.uid('user')
        with server.connect() as c:
            if c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone():parser.error('Email is already in use.')
            c.execute('INSERT INTO organisations VALUES(?,?,?,?)',(org,args.name[:180],'Fictional hospital demonstration',''))
            c.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?)',(user,org,'Hospital Administrator',email,server.password_hash(pw),'admin','Administration',''))
            server.audit(c,{'org':org,'id':user},'hospital_provisioned',org)
        print('Created isolated demonstration hospital:',org)
        print('Sign in as its administrator, then add doctors and reception users in Hospital settings.')
    elif args.command=='backup':
        destination=args.destination.resolve()
        if destination.exists():parser.error('Choose a new backup path; existing files will not be overwritten.')
        destination.parent.mkdir(parents=True,exist_ok=True)
        try:
            with server.connect() as source:
                target=sqlite3.connect(destination);source.backup(target);target.close()
            destination.chmod(0o600)
            print('Backup created:',destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    elif args.command=='check-backup':
        path=args.file.resolve()
        if not path.is_file():parser.error('Backup file does not exist.')
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as c:
            result=c.execute('PRAGMA integrity_check').fetchone()[0]
            docs=c.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        print(json.dumps({'integrity':result,'approved_documents':docs}))
        if result!='ok':raise SystemExit(1)

if __name__=='__main__':main()
