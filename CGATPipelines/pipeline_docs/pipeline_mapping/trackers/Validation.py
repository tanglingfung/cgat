import os, sys, re, types, itertools

from SphinxReport.Tracker import *
from MappingReport import *

class ExonValidationSummary( MappingTracker, SingleTableTrackerRows ):
    table = "exon_validation"
