import argparse
import csv
import sys
from app import connection


def run():
    parser = argparse.ArgumentParser(description="PAZME: статистика и заявки")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stats")
    commands.add_parser("export")
    remove = commands.add_parser("delete")
    remove.add_argument("phone")
    commands.add_parser("purge-expired")
    args = parser.parse_args()
    with connection() as db:
        if args.command == "stats":
            rows = db.execute("SELECT date,source,views,interested,declined,submissions FROM events ORDER BY date DESC, source").fetchall()
            print("дата | источник | открытия | да | нет | отправки телефона")
            for row in rows:
                print(" | ".join(str(field) for field in row))
            print("Всего актуальных телефонов:", db.execute("SELECT count(*) FROM leads").fetchone()[0])
            for role, cnt in db.execute("SELECT role,count(*) FROM leads GROUP BY role ORDER BY role"):
                print(("Участники" if role == "participant" else "Бизнес") + ":", cnt)
        elif args.command == "export":
            writer = csv.writer(sys.stdout)
            writer.writerow(["phone", "role", "source", "consent_at_utc", "consent_version"])
            writer.writerows(db.execute("SELECT phone,role,source,consent_at,consent_version FROM leads ORDER BY consent_at DESC"))
        elif args.command == "delete":
            digits = "".join(c for c in args.phone if c.isdigit())
            if len(digits) == 11 and digits[0] == "8":
                digits = "7" + digits[1:]
            cursor = db.execute("DELETE FROM leads WHERE phone=?", ("+" + digits,))
            print("Удалено записей:", cursor.rowcount)
        elif args.command == "purge-expired":
            cursor = db.execute("DELETE FROM leads WHERE consent_at < datetime('now','-6 months')")
            print("Удалено устаревших заявок:", cursor.rowcount)


if __name__ == "__main__":
    run()
