import argparse
import csv
import sys
from app import connection, EXPORT_HEADERS, export_rows


def run():
    p=argparse.ArgumentParser(description='PAZME: заявки и статистика')
    s=p.add_subparsers(dest='command',required=True)
    s.add_parser('stats');s.add_parser('export')
    rm=s.add_parser('delete');rm.add_argument('phone')
    s.add_parser('purge-expired')
    args=p.parse_args()
    with connection() as db:
        if args.command=='stats':
            print('дата | источник | открытия | да | нет | контакты | анкеты')
            for row in db.execute('SELECT date,source,views,interested,declined,submissions,profiles FROM events ORDER BY date DESC,source'):
                print(' | '.join(map(str,row)))
            print('Всего актуальных контактов:',db.execute('SELECT count(*) FROM leads').fetchone()[0])
            for role,cnt in db.execute('SELECT role,count(*) FROM leads GROUP BY role ORDER BY role'):
                print(('Участники' if role=='participant' else 'Бизнес')+':',cnt)
            print('С заполненными анкетами:',db.execute('SELECT count(*) FROM leads WHERE profile_at IS NOT NULL').fetchone()[0])
        elif args.command=='export':
            writer=csv.writer(sys.stdout)
            writer.writerow(EXPORT_HEADERS)
            writer.writerows(__import__('app').safe_csv_row(r) for r in export_rows(db))
        elif args.command=='delete':
            if '@' in args.phone:
                cursor=db.execute('DELETE FROM leads WHERE email=?',(args.phone.strip().lower(),))
            else:
                digits=''.join(c for c in args.phone if c.isdigit())
                if len(digits)==11 and digits[0]=='8':digits='7'+digits[1:]
                cursor=db.execute('DELETE FROM leads WHERE phone=?',('+'+digits,))
            print('Удалено записей:',cursor.rowcount)
        elif args.command=='purge-expired':
            cursor=db.execute("DELETE FROM leads WHERE consent_at < datetime('now','-6 months')")
            print('Удалено устаревших заявок:',cursor.rowcount)

if __name__=='__main__':run()
