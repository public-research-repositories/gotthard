#!/usr/bin/env python
# Adapted from code in this answer: http://stackoverflow.com/a/30734789
# From the SO question: http://stackoverflow.com/questions/2887878/importing-a-csv-file-into-a-sqlite3-database-table-using-python
import csv, sqlite3
import io
import logging
import sys

def _get_col_datatypes(fin):
    dr = csv.DictReader(fin, delimiter='\t') # comma is default delimiter
    fieldTypes = {}
    for entry in dr:
        feildslLeft = [f for f in dr.fieldnames if f not in fieldTypes.keys()]
        if not feildslLeft: break # We're done
        for field in feildslLeft:
            data = entry[field]

            # Need data to decide
            if len(data) == 0:
                continue

            if data.isdigit():
                fieldTypes[field] = "INTEGER"
            else:
                fieldTypes[field] = "TEXT"
        # TODO: Currently there's no support for DATE in sqllite

    if len(feildslLeft) > 0:
        raise Exception("Failed to find all the columns data types - Maybe some are empty?")

    return fieldTypes


def escapingGenerator(f):
    skip_first_line = True
    for line in f:
        if skip_first_line:
            skip_first_line = False
            continue
        yield line.encode("ascii", "xmlcharrefreplace").decode("ascii")



def tsvToDb(file=None, filename=None, storage=None, table_name='t'):
    if not filename is None:
        with io.open(filename, mode='r', encoding="ISO-8859-1") as fin:
            return tsvFileToDb(fin, storage=storage)
    else:
        return tsvFileToDb(file, storage=storage)


def tsvFileToDb(fin, storage=None, table_name='t'):
    dt = _get_col_datatypes(fin)

    fin.seek(0)

    reader = csv.DictReader(fin, delimiter='\t')

    # Keep the order of the columns name just as in the CSV
    fields = reader.fieldnames
    cols = []
    #print "have fields", fields

    # Set field and type
    for f in fields:
        cols.append("%s %s" % (f, dt[f]))

    # Generate create table statement:
    stmt = "CREATE TABLE %s (%s)" % (table_name, ",".join(cols))

    if storage is None: storage = ":memory:"
    con = sqlite3.connect(storage)
    cur = con.cursor()
    cur.execute(stmt)

    fin.seek(0)


    reader = csv.reader(escapingGenerator(fin), delimiter='\t')

    # Generate insert statement:
    stmt = "INSERT INTO %s VALUES(%s);" % (table_name, ','.join('?' * len(cols)))

    cur.executemany(stmt, reader)
    con.commit()

    return con

if __name__ == 'main':
    if len(sys.argv) < 3:
        print "Usage %s FILE.TSV|- OUT.DB"
        sys.exit(1)

    in_filename = sys.argv[1]
    out_filename = sys.argv[2]
    if in_filename == "-":
        tsvToDb(file=sys.stdin, storage=out_filename)
    else:
        tsvToDb(filename=sys.stdin, storage=out_filename)

