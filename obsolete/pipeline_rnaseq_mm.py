################################################################################
#
#   MRC FGU Computational Genomics Group
#
#   $Id: pipeline_rnaseq_geneset.py 2900 2012-03-38 14:38:00Z david $
#
#   Copyright (C) 2012 David Sims
#
#   This program is free software; you can redistribute it and/or
#   modify it under the terms of the GNU General Public License
#   as published by the Free Software Foundation; either version 2
#   of the License, or (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software
#   Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#################################################################################
"""
========================
RNAseq Geneset Pipeline
========================

:Author: David Sims 
:Release: $Id: pipeline_rnaseq_genesst.py 2900 2012-03-28 14:38:00Z david $
:Date: |today|
:Tags: Python

The RNAseq geneset pipeline parses multiple GTF files derived from RNAseq experiments in different tissues and produces a consensus geneset

Usage
=====

See :ref:`PipelineSettingUp` and :ref:`PipelineRunning` on general information how to use CGAT pipelines.

Configuration
-------------

The pipeline requires a configured :file:`pipeline.ini` file. 

Configuration files follow the ini format (see the python
`ConfigParser <http://docs.python.org/library/configparser.html>` documentation).
The configuration file is organized by section and the variables are documented within 
the file. In order to get a local configuration file in the current directory, type::

    python <codedir>/pipeline_rnaseq_genest.py config

The sphinxreport report requires a :file:`conf.py` and :file:`sphinxreport.ini` file 
(see :ref:`PipelineDocumenation`). To start with, use the files supplied with the
:ref:`Example` data.


Input
-----

Input are GTF-formatted files. 

Requirements
------------

The pipeline requires the information from the following pipelines:

:doc:`pipeline_annotations`

set the configuration variables:
   :py:data:`annotations_database` 
   :py:data:`annotations_dir`

On top of the default CGAT setup, the pipeline requires the following software to be in the 
path:

+--------------------+-------------------+------------------------------------------------+
|*Program*           |*Version*          |*Purpose*                                       |
+--------------------+-------------------+------------------------------------------------+
|BEDTools            |                   |interval comparison                             |
+--------------------+-------------------+------------------------------------------------+


Pipline Output
==============

The results of the computation are all stored in an sqlite relational
database :file:`csvdb`.


Code
====

"""
import sys
import tempfile
import optparse
import shutil
import itertools
import csv
import math
import random
import re
import glob
import os
import shutil
import collections
import gzip
import sqlite3
import pysam
import CGAT.IndexedFasta as IndexedFasta
import CGAT.IndexedGenome as IndexedGenome
import CGAT.FastaIterator as FastaIterator
import CGAT.Genomics as Genomics
import CGAT.IOTools as IOTools
import CGAT.MAST as MAST
import CGAT.GTF as GTF
import CGAT.GFF as GFF
import CGAT.Bed as Bed
import cStringIO
import numpy
import CGAT.Masker as Masker
import fileinput
import CGAT.Experiment as E
import logging as L
from ruffus import *

USECLUSTER = True

###################################################
###################################################
###################################################
## Pipeline configuration
###################################################
import CGAT.Pipeline as P
P.getParameters(  ["pipeline.ini", ] )
PARAMS = P.PARAMS
#PARAMS_ANNOTATIONS = P.peekParameters( PARAMS["geneset_dir"],"pipeline_annotations.py" )

###################################################
###################################################        
###################################################
## Parse transcripts from GTF file
@transform( "*.gff", regex(r"(\S+).gff"), r"\1.gtf" )
def convertGffToGtf( infile, outfile ):
    '''Convert  txt to Gtf'''
    track = P.snip( os.path.basename(infile), ".gff" )
    statement = '''cat %(infile)s | awk 'OFS="\\t" {print $1,$2,$3,$4,$5,$6,$7,$8,"transcript_id \\""$9"\\"; gene_id \\""$9"\\";"}' > %(outfile)s'''
    P.run()
    
###################################################
@transform( convertGffToGtf, regex(r"(\S+).gtf"), r"\1.transcripts.gtf" )
def getGtfStrandedTranscripts( infile, outfile ):
    '''join exons to get transcripts from GTF file'''
    track = P.snip( os.path.basename(infile), ".gtf" )
    statement = '''cat %(infile)s | python %(scriptsdir)s/gtf2gtf.py --join-exons --log=%(outfile)s.log | sort -k1,1 -k4,4n > %(outfile)s'''
    P.run()
    
###################################################
@transform( getGtfStrandedTranscripts, regex(r"(\S+).gtf"), r"\1.bed.gz" )
def convertStrandedTranscriptsToBed( infile, outfile ):
    '''Convert GTF to compressed BED file'''
    track = P.snip( os.path.basename(infile), ".gtf" )
    statement = '''cat %(infile)s | python %(scriptsdir)s/gff2bed.py --is-gtf --log=%(outfile)s.log | sort -k1,1 -k2,2n | gzip > %(outfile)s'''
    P.run()
    
###################################################
@transform( getGtfStrandedTranscripts, suffix(".gtf"), ".novel.gtf")
def getNovelGeneset( infile, outfile ):
    '''identify transcrpts that overlap an ensembl coding gene '''
    ensembl_genes = PARAMS["ensembl_genes"]
    ensembl_noncoding = PARAMS["ensembl_noncoding"]
    # need to remove transcripts that overlap 100% with noncoding transcripts
    statement = '''cat %(infile)s 
                   | intersectBed -a stdin -b %(ensembl_genes)s -v -s 
                   | intersectBed -a stdin -b %(ensembl_noncoding)s -v -s 
                   | sort -k1,1 -k4,4n > %(outfile)s'''
    P.run()

###################################################
@transform( getNovelGeneset, suffix(".gtf"), ".rename.gtf")
def renameTranscripts( infile, outfile ):
    '''systematically rename transcripts '''
    statement = '''cat %(infile)s | awk 'OFS="\\t" {print $1,$2,$3,$4,$5,$6,$7,$8,"transcript_id \\"rnaseq_es_novel_transcript_"NR"\\"; gene_id \\"rnaseq_es_novel_gene_"NR"\\"; "}' > %(outfile)s;'''
    P.run()
    

############################################################
############################################################
############################################################
## TRANSCRIPTION START SITES
@transform(renameTranscripts, regex(r"(\S+).rename.gtf"), r"mm_es_rnaseq_novel_transcripts.bed.gz" )
def buildTranscriptTSS( infile, outfile ):
    '''annotate transcription start sites from reference gene set.
    Similar to promotors, except that the witdth is set to 1. '''
    statement = """
        cat < %(infile)s 
        | python %(scriptsdir)s/gff2gff.py --sanitize=genome --skip-missing --genome-file=%(genome_dir)s/%(genome)s --log=%(outfile)s.log  
        | python %(scriptsdir)s/gtf2gff.py --method=promotors --promotor=1 --genome-file=%(genome_dir)s/%(genome)s --log=%(outfile)s.log 
        | python %(scriptsdir)s/gff2bed.py --is-gtf --name=transcript_id --log=%(outfile)s.log 
        | python %(scriptsdir)s/bed2bed.py --method=filter-genome --genome-file=%(genome_dir)s/%(genome)s --log %(outfile)s.log
        | gzip
        > %(outfile)s """
    P.run()
    
###################################################
@transform( buildTranscriptTSS, suffix(".bed.gz"), ".extended.bed.gz" )
def ExtendRegion( infile, outfile ):
    '''convert bed to gtf'''
    statement = """gunzip < %(infile)s 
                   | slopBed -i stdin -g %(faidx)s -b 1000  
                   | gzip
                   > %(outfile)s """
    P.run()
    
###################################################
@transform( (buildTranscriptTSS,ExtendRegion), suffix(".bed.gz"), ".gtf" )
def convertToGTF( infile, outfile ):
    '''convert bed to gtf'''
    statement = """gunzip < %(infile)s 
                   | python %(scriptsdir)s/bed2gff.py --as-gtf  --log=%(outfile)s.log 
                   > %(outfile)s """
    P.run()

                
############################################################
############################################################
############################################################

@follows( convertGffToGtf, getGtfStrandedTranscripts,
          getNovelGeneset, renameTranscripts)
def transcripts():
    '''build all targets.'''
    pass
    
@follows( buildTranscriptTSS, ExtendRegion, convertToGTF )
def tss():
    '''build all targets.'''
    pass 

@follows( transcripts, tss )
def full():
    '''build all targets.'''
    pass 


if __name__== "__main__":
    sys.exit( P.main(sys.argv) )
    
