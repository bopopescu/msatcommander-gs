#!/usr/bin/env python
# encoding: utf-8
"""
BlatGenomicScreener.py

Created by Brant Faircloth on 2009-05-22.
Copyright (c) 2009 Brant Faircloth. All rights reserved.

Takes fasta file as input, and screens content against genomic data
(.2bit file) using Blat as the sequence comparison engine and writing data to 
a SQLite dbase.

"""
import os, sys, pdb, time, MySQLdb, subprocess, ConfigParser, tempfile, cPickle, multiprocessing, textwrap, optparse
from math import log
#from Bio import SeqIO

def db_write(cur, db_row):
    cur.execute('''INSERT INTO blat (id, q_name, t_name, strand, percent, 
    length, mismatches, q_gaps, q_size, q_start, q_end, t_size, t_start, 
    t_end, e_score, bit_score) 
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''', 
    (db_row['pkey'], db_row['qName'], db_row['tName'], db_row['strand'], 
    db_row['percent'], db_row['length'], db_row['misMatch'], db_row['qGaps'], 
    db_row['qSize'], db_row['qStart'], db_row['qEnd'], db_row['tSize'], 
    db_row['tStart'], db_row['tEnd'], db_row['eScore'], db_row['bitScore']))
    # go ahead and commit since there will be very, very many writes
    #cur.commit()

def primaryMatchCount(cur, pkey, match_count):
    cur.execute('''UPDATE sequence_test SET match_count = %s WHERE id = %s''', (match_count, pkey))

def pslPercentId(psl, protein=False, mRNA=True):
    '''
    convert the percent sequence ID of an alignment from a single line of a 
    parsed PSL file.  Code adapted from 
    http://genome.ucsc.edu/FAQ/FAQblat#blat4
    '''
    millibad = 0
    if protein:
        sizeMul = 3
    else:
        sizeMul = 1
    qAliSize = sizeMul * (psl['qEnd'] - psl['qStart'])
    tAliSize = psl['tEnd'] - psl['tStart']
    aliSize = min(qAliSize, tAliSize)
    if aliSize <= 0:return 0
    else:
        sizeDif = qAliSize - tAliSize
        if sizeDif < 0:
            if mRNA:
                sizeDif = 0;
            else:
                sizeDif = -sizeDif
        insertFactor = psl['qNumInsert']
        if not mRNA:
            insertFactor += psl['tNumInsert']
        total = (sizeMul * (psl['match'] + psl['repMatch'] + psl['misMatch']))
        if total != 0:
            milliBad = (1000 * (psl['misMatch']*sizeMul + insertFactor + round(3*log(1+sizeDif)))) / total
        percent = round(100 - milliBad * 0.1,0)
        psl['percent'] = percent
        return psl

def pslScore(psl, sizeMul = 1):
    '''
    convert the score of an alignment from a single line of a parsed PSL file.
    Code adapted from http://genome.ucsc.edu/FAQ/FAQblat#blat4
    '''
    score = sizeMul * (psl['match'] + (psl['repMatch'] >> 1)) - sizeMul * psl['misMatch'] - psl['qNumInsert'] - psl['tNumInsert']
    psl['score'] = score
    return psl


def parse_blat(cur, pkey, blat_result, seq_name, seq_seq):
    percent = 90.0
    length = 25
    e_score = 1e-10
    fieldnames = ['qName','tName', 'percent', 'length', 'misMatch', 'qGaps', 'qStart', 'qEnd', 'tStart', 'tEnd', 'eScore', 'bitScore']
    match_count = 0
    self_match = False
    unique_matches = []
    for row in blat_result:
        row = row.split()
        # merge fieldnames with row data
        db_row = dict(zip(fieldnames, row))
        # covert strings to ints
        for f in ['length', 'misMatch', 'qGaps', 'qStart', 'qEnd', 'tStart', 'tEnd',]:
            db_row[f] = int(db_row[f])
        for f in ['percent', 'eScore', 'bitScore']:
            db_row[f] = float(db_row[f])
        # determine strand
        if db_row['tStart'] > db_row['tEnd']:
            db_row['tStart'], db_row['tEnd'] = db_row['tEnd'], db_row['tStart']
            db_row['strand'] = '-'
        else:
            db_row['strand'] = '+'
        # fix/add some other metrics
        db_row['percent'] = round(db_row['percent'],1)
        db_row['qSize'] = db_row['qEnd'] - db_row['qStart']
        db_row['tSize'] = db_row['tEnd'] - db_row['tStart']
        # if this is NOT a self-match
        if not (db_row['qName'] == db_row['tName']) and db_row['percent'] >= percent and db_row['length'] >= length and db_row['eScore'] <= e_score:
            # update the dependent table
            db_row['pkey'] = pkey
            db_write(cur, db_row)
            if db_row['tName'] not in unique_matches:
                unique_matches.append(db_row['tName'])
                match_count += 1
        elif (db_row['qName'] == db_row['tName']):
            # self match
            pass
    # update the primary table with the match count
    primaryMatchCount(cur,pkey,match_count)
    #pdb.set_trace()

def worker(tb, pkey, name, seq_trimmed):
        conn = MySQLdb.connect(user="python", passwd="BgDBYUTvmzA3", 
        db="454_msatcommander")
        cur = conn.cursor()
        sequence = ('>%s\n%s\n' % (name, seq_trimmed))
        #pdb.set_trace()
        blat_result, blat_error = subprocess.Popen('/Users/bcf/bin/i386/blat %s stdin -mask=lower -out=blast8 -noHead stdout' % tb, shell=True, stdin = subprocess.PIPE, stdout = subprocess.PIPE, stderr=subprocess.PIPE).communicate(input=sequence)
        if blat_error:
            pdb.set_trace()
        if blat_result and not blat_error:
            # drop the ending newline
            blat_result = blat_result.split('\n')[:-1]
            parse_blat(cur, pkey, blat_result, name, seq_trimmed)
            #print 'parsing'
        elif not blat_result and not blat_error:
            #pdb.set_trace()
            primaryMatchCount(cur, pkey, 0)
        elif blat_error:
            print "blat processing error: %s" % blat_error
            #pdb.set_trace()
        conn.commit()
        cur.close()
        conn.close()

def createBlatTable(cur):
    '''add a table to the database'''
    try:
        # if previous tables exist, drop them
        # TODO: fix createDbase() to drop tables safely
        cur.execute('''DROP TABLE blat''')
    except:
        pass
        # TODO:  if using pooled column, add that bastard here to save time
    cur.execute('''CREATE TABLE blat (id INT UNSIGNED NOT NULL, q_name 
        VARCHAR(100), t_name VARCHAR(100), strand VARCHAR(1), percent 
        DECIMAL(4,1), length SMALLINT unsigned, mismatches SMALLINT UNSIGNED, 
        q_gaps SMALLINT UNSIGNED, q_size SMALLINT UNSIGNED, q_start SMALLINT 
        UNSIGNED, q_end SMALLINT UNSIGNED, t_size SMALLINT UNSIGNED, t_start 
        SMALLINT UNSIGNED, t_end SMALLINT UNSIGNED, e_score FLOAT, bit_score 
        FLOAT, INDEX blat_id (id), INDEX blat_q_name (q_name))''')
        
def updateSequenceTable(cur):
    #pdb.set_trace()
    try:
        cur.execute('''ALTER TABLE sequence_test ADD COLUMN match_count SMALLINT UNSIGNED''')
        print 'Added match_count column'
    except MySQLdb._mysql.OperationalError, e:
        if e[0] == 1060:
            cur.execute('''UPDATE sequence_test SET match_count = NULL''')
            print 'Zeroed-out match_count column'
    except:
        print "Error adding match_count to sequence table"
        sys.exit()


def fasta(name, seq):
    return '>%s\n%s\n' % (name, seq)

def motd():
    motd = '''
    ##############################################################
    #                     msatcommander 454                      #
    #                                                            #
    # - parsing and error correction for sequence tagged primers #
    # - microsatellite identification                            #
    # - sequence pooling                                         #
    # - primer design                                            #
    #                                                            #
    # Copyright (c) 2009 Brant C. Faircloth & Travis C. Glenn    #
    ##############################################################\n
    '''
    print motd

def interface():
    usage = "usage: %prog [options]"

    p = optparse.OptionParser(usage)

    p.add_option('--configuration', '-c', dest = 'conf', action='store', \
type='string', default = None, help='The path to the configuration file.', \
metavar='FILE')

    (options,arg) = p.parse_args()
    if not options.conf:
        p.print_help()
        sys.exit(2)
    if not os.path.isfile(options.conf):
        print "You must provide a valid path to the configuration file."
        p.print_help()
        sys.exit(2)
    return options, arg

def main():
    start_time = time.time()
    options, arg = interface()
    motd()
    print 'Started: ', time.strftime("%a %b %d, %Y  %H:%M:%S", time.localtime(start_time))
    conf = ConfigParser.ConfigParser()
    conf.read(options.conf)
    if conf.getboolean('Multiprocessing', 'MULTIPROCESSING'):
        # get num processors
        n_procs = conf.get('Multiprocessing','processors')
        if n_procs == 'Auto':
            n_procs = multiprocessing.cpu_count() - 1
        else:
            n_procs = int(n_procs)
        print 'Multiprocessing.  Number of processors = ', n_procs
    else:
        n_procs = 1
        print 'Not using multiprocessing. Number of processors = ', n_procs
    # create a table for the output
    conn = MySQLdb.connect(user="python", passwd="BgDBYUTvmzA3", 
    db="454_msatcommander")
    cur = conn.cursor()
    createBlatTable(cur)
    updateSequenceTable(cur)
    conn.commit()
    cur.close()
    conn.close()
    # get clusters from conf file
    cluster = [c[1] for c in conf.items('Clusters')]
    # create a temp directory for results
    tdir = tempfile.mkdtemp(prefix='msatcommander-454-')
    #print tdir
    # get sequences from Dbase by cluster
    for c in cluster:
        # get all sequence to build the 2bit file
        cur.execute('''SELECT id, name, seq_trimmed FROM sequence_test WHERE cluster = %s''', (c,))
        sequences = cur.fetchall()
        if sequences:
            tf = tempfile.mkstemp(prefix='clean-%s-' % c, suffix='.fa', dir=tdir)
            tf_handle = open(tf[1], 'w')
            for s in sequences:
                # TODO:  switch over to id only for fasta to reduce MySQL index overhead.
                tf_handle.write(fasta(s[1],s[2]))
            tf_handle.close()
            print "Masking low complexity regions in:\t%s" % tf[1]
            if n_procs > 1:
                lc_masker_out, lc_masker_err = subprocess.Popen('/Users/bcf/bin/RepeatMasker/repeatmasker -qq -noint -xsmall -pa 7 %s' % tf[1], shell=True, stdout=subprocess.PIPE, stderr = subprocess.PIPE).communicate()
            else:
                lc_masker_out, lc_masker_err = subprocess.Popen('/Users/bcf/bin/RepeatMasker/repeatmasker -qq -noint -xsmall %s' % tf[1], shell=True, stdout=subprocess.PIPE, stderr = subprocess.PIPE).communicate()
            # for each cluster generate the 2bit file for blat
            masked_tf = tf[1] + '.masked'
            tb = tf[1] + '.2bit'
            print "Building twobit file from:\t\t%s" % masked_tf
            two_bit = subprocess.Popen('/Users/bcf/bin/i386/faToTwoBit %s %s' % (masked_tf, tb), shell=True, stderr = subprocess.PIPE).communicate()
            # get only msat-containing sequences
            cur.execute('''SELECT id, name, seq_trimmed FROM sequence_test WHERE cluster = %s and msat = 1''', (c,))
            sequences = cur.fetchall()
            if n_procs > 1:
                threads = []
                index = 0
                while index < len(sequences):
                #while index < 50:
                    if len(threads) < n_procs:
                        p = multiprocessing.Process(target=worker, args=(tb, sequences[index][0], sequences[index][1], sequences[index][2]))
                        p.start()
                        threads.append(p)
                        index += 1
                        #if index%1000 == 0:
                        #    print "Sequence # %s, %s" % (index, time.strftime("%a %b %d, %Y  %H:%M:%S", time.localtime(time.time())))
                    else:
                        for t in threads:
                            if not t.is_alive():
                                threads.remove(t)
                # clean up any running processes before moving along to next cluster
                while threads:
                    for t in threads:
                        if not t.is_alive():
                            threads.remove(t)
            else:
                index = 0
                #while index < len(sequences):
                while index < 50:
                    # send 0th item of list to worker and remove
                    worker(tb, sequences[index][0], sequences[index][1], sequences[index][2])
                    index += 1
    end_time = time.time()
    print 'Ended: ', time.strftime("%a %b %d, %Y  %H:%M:%S", time.localtime(end_time))
    print '\nTime for execution: ', (end_time - start_time)/60, 'minutes'
    #pdb.set_trace()


if __name__ == '__main__':
    main()
