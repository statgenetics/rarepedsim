#!/usr/bin/env python
#
# This file is part of the SEQPower program
# Copyright (c) 2013, Biao Li <libiaospe@gmail.com, biaol@bcm.edu>
# GNU General Public License (http://www.gnu.org/licenses/gpl.html)
#
# Author: Biao Li (adapted from srv, Peng and Liu 2010)
# Date: 11-15-2013
# Co-author: Gao Wang (a number of fixes including changes to default values and updated demographic and selection models)
# Date: 05-16-2014
# Purpose: Simulation of samples with rare variants & generation of variant data pool.
#
'''
|**Purpose** 
    Repeatedly simulating a population of sequences forward in time, subject to mutation,
    natural selection and demography. Because most mutants are introduced
    during the rapid population expansion, most of the alleles will be rare at the
    end of any replicate of the simulation. Samples simulated using this script can be used to study
    genetic diseases caused by a large number of rare variants.
|
|**Standard Output**
    The standard output of the program will be a *.sfs file, which contains variant
    site-specific information of each simulated replicate, such as minor allele
    frequency (maf), position (pos), selection coefficient (sel), etc. Each row
    corresponds to each mutant site for a replicate. Columns are replicate #, maf,
    sel and pos information.
|
|**Optional Output**
    For any replicate of simulation users have the option of saving genotype information
    of all individuals into a file in standard .ped format with file name *_rep_i.ped, where
    * will be replaced by parameter 'Output Files Name (prefix)'. Each line of the file
    represents each individual. The standard .ped format begins with six columns of
    'familyID', 'ID', 'fatherID', 'MotherID', 'sex' and 'affectionStatus', followed
    by m (m = 2 * number of mutant sites, ploidy = 2 in human genome columns of genotype
    information. Because there is no family structure, we have 'familyID' =
    1,2,3,...; 'ID' = 1; 'fatherID' = 'motherID' = 0; 'sex' = 1 for male and 2 for female;
    'affectionStatus' = 1 for unaffected and 2 for affected. In addition, wildtype and mutant
    alleles are denoted by 0 and 1 respectively. 
|    
'''

#
# This script simulates mutants in mutational space. That is to say, mutants
# are stored as locations on chromosomes, instead of allele number. Because
# indiviudals have different number of mutants, zeros are used to fill the
# rest of the markers.
#

import simuOpt
simuOpt.setOptions(alleleType='long', optimized=True, quiet=True, version='1.0.5', gui='batch')

import simuPOP as sim
from simuPOP.utils import ProgressBar, migrIslandRates
from simuPOP.sandbox import RevertFixedSites, revertFixedSites, MutSpaceSelector, MutSpaceMutator, MutSpaceRecombinator

import os, sys, logging, math, time, random, tempfile, tarfile, shutil, glob
import numpy as np

from gdata import GData


options = [
    {'separator': 'Basics'},
    {'name': 'regRange',
     'default': [1800, 1800],
     'label': '    Gene Length (range)',
     'description': ''' *** Gene Length (range) ***
        |The length of gene region for each time of evolution is
        taken as a random number within the range of region. If a fixed number
        of gene length N is required, this parameter should be set as [N, N].''',
     'type': 'integers',
     'validate': 'len(regRange) == 2',
    },
    {'name': 'fileName',
     'default': 'MySimuRV',
     'label': '    Output Files Name (prefix)',
     'description': ''' *** Output Files Name (prefix) *** ''',
     'type': str,
    },
    {'name': 'numReps',
     'default': 1,
     'label': '    Number of Replicates',
     'description': ''' *** Number of Replicates *** ''',
     'type': 'integer',
    },
    {'separator': ''},
    {'separator': 'Demographic Model'},
    {'name': 'N',
     'default': [10000, 10000, 5633, 654000],
     'label': '    Effective Population Sizes',
     'description': ''' *** Effective Population Sizes ***
        |Assuming a n (n = array length - 1) stage demographic model,
        this parameter specifies population sizes at the beginning of evolution
        and at the end of each stage N_0,...,N_n. If N_i < N_i+1, an exponential
        population expansion model will be used to expand population from size N_i
        to N_i+1. If N_i > N_i+1, an instant population reduction will reduce
        population size to N_i+1.
        For example, 
        | N=[5000, 5000, 800, 30000],
        | which simulates a three stage demographic model where a population
        | beginning with 5000 individuals first undergoes a burn-in stage with
        | constant population size 5000, then goes through a bottleneck of 800
        | indiviudals, and after that expands exponentially to a size of 30000.
        | Default value comes from Gazave E. et al. (2013).
        ''',
     'type': 'integers',
    },
    {'name': 'G',
     'default': [1000, 479, 141],
     'label': '    Numbers of Generations per Stage',
     'description': ''' *** Numbers of Generations per Stage ***
        |Numbers of generations of each stage of a n stage demographic model.
        This parameter should have n elements, in comparison to n+1 elements for parameter
        N (Effective Population Sizes).  Default value comes from Gazave E. et al. (2013).''',
     'type': 'integers',
     'validate': 'len(G) + 1 == len(N)'
    },
    {'separator': ''},
    {'separator': 'Genetic forces'},
    {'name': 'mutationModel',
     'default': 'finite_sites',
     'label': '    Mutation Model',
     'type': ('chooseOneOf', ['infinite_sites', 'finite_sites']),
     'description': ''' *** Mutation Model ***
        |Mutation model. The default mutation model is a finite-site
        model that allows mutations at any locus. If a mutant is mutated, it will be
        back-mutated to a wildtype allele. Alternatively, an infinite-sites model can
        be simulated where new mutants must happen at loci without existing mutants,
        unless no vacant locus is available (a warning message will be printed 
        in that case).''',
    },
    {'name': 'mu',
     'default': 1.8e-8,
     'label': '    Mutation Rate',
     'description': ''' *** Mutation Rate ***
        |Mutation rate per base pair''',
     'type': 'number',
     'validator': simuOpt.valueBetween(0., 1.),
    },
    {'name': 'revertFixedSites',
     'default': False,
     'label': '   Revert Fixed Sites?',
     'description': ''' *** Revert Fixed Sites? ***
        | Whether or not to revert fixed mutant sites to wild-type sites''',
    'type': bool,
    },
    {'name': 'selModel',
     'default': 'additive',
     'label': '    Multi-locus Selection Model',
     'type': ('chooseOneOf', ('multiplicative', 'additive', 'exponential')),
     'description': ''' *** Multi-locus Selection Model ***
        |Multi-locus selection model, namely how to obtain an
        overall individual fitness after obtaining fitness values at all loci.
        This script supports three models.
        | -- multiplicative: Product of individual fitness.
        | -- additive: One minus the combined selection deficiencies.
        | -- exponential: Exponential of combined selection deficiencies.
        |**Note** Each fitness can be equal to or greater than zero, which
        represents neutral loci, or loci under positive selection.''',
    },
    {'name': 'selDist',
     'default': 'Kyrukov_2009_European',
     'label': '    Selection Coefficient Distribution Model',
     'type': ('chooseOneOf', ['constant', 'Eyre-Walker_2006',
                              'Boyko_2008_African', 'Boyko_2008_European',
                              'Kyrukov_2009_European', 'mixed_gamma']),
     'description': ''' *** Selection Coefficient Distribution Model ***
        |Distribution of selection coefficient for new mutants.
        Each distribution specifies s (selection coefficient) and h (dominance
        coefficient, default to 0.5 for additivity) that assign fitness values
        1, 1-hs and 1-s for genotypes AA (wildtype), Aa and aa, respectively.
        Note that positive s is used for negative selection so negative s is
        needed to specify positive selection. Note that we use 2k in the default
        distribution of Gamma distributions because theoretical estimates of 
        s is for each mutant with 1-2s as fitness value for genotype aa in
        our model. This script currently supports the following distributions.
        | -- constant: A single selection coefficient that gives each mutant a
        |    constant value s. The default parameter for this model is 
        |    0.01, 0.5. You can set selCoef to [0, 0] to simulate neutral 
        |    cases or a negative value for positive selection.
        | -- Eyre-Walker_2006: A basic gamma distribution assuming a constant
        |    population size model (Eyre-Walker et al, 2006). The default
        |    parameters for this model is Pr(s=x)=Gamma(0.23, 0.185*2), with
        |    h=0.5.
        |    A scaling parameter 0.185*2 is used because s in our simulation
        |    accounts for 2s for Eyre-Walker et al.
        | -- Boyko_2008_African: A gamma distribution assuming a two-epoch 
        |    population size change model for African population 
        |    (Boyko et al, 2008). The default parameters for this model is 
        |    Pr(s=x)=Gamma(0.184, 0.160*2), with h=0.5.
        | -- Boyko_2008_European: A gamma distribution (for s) assuming a 
        |    complex bottleneck model for European population 
        |    (Boyko et al, 2008). The default parameters for this model is
        |    Pr(s=x)=Gamma(0.206, 0.146*2) with h=0.5.
        | -- Boyko_2008_European: A gamma distribution (for s) assuming a 
        |    complex bottleneck model for European population 
        |    (Boyko et al, 2008). The default parameters for this model is
        |    Pr(s=x)=Gamma(0.206, 0.146*2) with h=0.5.
        | -- Kyrukov_2009_European: A gamma distribution (for s) assuming a 
        |    complex bottleneck model for European population 
        |    (Boyko et al, 2009). The default parameters for this model is
        |    Pr(s=x)=Gamma(0.562341, 0.01) with h=0.5
        |**Note**: (1) For all models above we assume 37% variants to have
        s=0 for silent mutations. The Gamma models refer to the rest 63% of variants.
        (2) If you would like to define your own selection model, please define your
        own function and pass it to parameter 'Customized Selection Coefficient'
        next to this one in the interface .''',
    },
    {'name': 'selCoef',
     'default': [],
     'label': '    Customized Selection Coefficient',
     'description': ''' *** Customized Selection Coefficient ***
        |Customized selection coefficient distribution model.
        If None is given, the default value of distribution selected in the
        previous parameter 'Selection Coefficient Distribution Model' will be used.
        **Note** Length of this parameter determines which type of model to use
                 and values specify distribution coefficients.
        | -- length = 0: [] --  no customized input.
        | -- length = 2: [s, h] -- constant selection coefficient (s) and 
        |        dominance coefficient (h).
        | -- length = 3: [k, d, h] -- gamma distributed selection 
        |        coefficient, where k, d are shape, scale parameters of 
        |        gamma distribution and h is dominance coefficient.
        | -- length = 5: [p, s, k, d, h] -- mixed gamma distributed 
        |        selection coefficient, where p is the probability of having
        |        the selection coefficient equal to s; k, d are shape and
        |        scale parameters of gamma distribution; h is the dominance
        |        coefficient.
        | -- length = 7: [p,s,k,d,h,l,u] -- truncated mixed gamma where values with s<l
        |        or s>u will be truncated.
        | -- length = 8: [p,s,q,k1,d1,k2,d2,h] -- complex mixed gamma
        |        distribution that can generate a mix of constant, positive
        |        gamma and negative gamma distributions. The negative
        |        distribution represents protective variant sites with
        |        negative selection coefficients, where q is the probability
        |        of having the selection coefficient following a positive
        |        gamma distribution with shape/scale parameters k1/d1. Thus,
        |        the probability of having selection coefficient following
        |        a negative/opposite gamma distribution is 1-p-q. The negative
        |        gamma distribution takes parameters k2 and d2.
        | -- length = 12: [p,s,q,k1,d1,k2,d2,h,l1,u1,l2,u2] -- truncated complex mixed gamma 
        |        where for [k_i,d_i] gamma distribution values with |s|<l_i or |s|>u_i will be
        |        truncated.
        |For example,
        | Parameter [0.001, 0] for a constant model defines a recessive model with fixed s.
        The Boyko_2008_European model is in fact [0.37, 0.0, 0.184, 0.160*2, 0.5] for
        Prob(s=0.0)=0.37 (neutral or synonymous)
        and Prob(s=x)=(1-0.37)*Gamma(0.184,0.160*2).
        ''',
     'type': list,
     'validate': 'len(selCoef) in [0,2,3,5,7,8,12]',
    },
    {'name': 'selRange',
     'default': [0.00001, 0.1],
     'label': '    Allowed range of selection coefficients',
     'type': list,
     'validate': 'len(selRange) == 2',
     'description': '''Generated selection coefficient is binned by range limits [l,u].
     for values of |s| < l these values will be set to |s| = l ; for values of |s| > u
     these values will be set to |s| = u'''
    },
    {'name': 'recRate',
     'default': 0,
     'label': '    Recombination Rate',
     'type': 'number',
     'description': ''' *** Recombination Rate ***
        |Recombination rate per base pair. If r times loci distance
        is greater than 0.5, a rate of 0.5 will be used.''',
    },
    #
    {'separator': ''},
    {'separator': 'Screen Output'},
    {'name': 'verbose',
     'default': 1,
     'label': '    Screen Output Mode (-1, 0 or 1)', 
     'type': int,
     'description': ''' *** Screen Output Mode (-1, 0 or 1) ***
        | -1 -- quiet, no screen output. 
        | 0 -- minimum, minimum output of simulation progress and time spent for
        |      each replicate.
        | 1 -- regular, regular screen output of statistics, simulation
        |      progress and time spent.
        ''',
     'validator': simuOpt.valueBetween(-1, 1),
    },
    {'name': 'steps',
     'default': [100],
     'label': '    Detailed Screen Output Interval per Stage',
     'description': ''' *** Detailed Screen Output Interval per Stage ***
        |Calculate and output statistics at intervals of specified
        number of generations. A single number or a list of numbers for each stage
        can be specified. If left unspecified, statistics from the beginning to
        the end of every generation of each stage will be printed out.''',
     'type': 'integers',
    },
    #
    {'separator': ''},
    {'separator': 'Optional File Output'},
    {'name': 'saveGenotype',
     'default': 0,
     'label': '    Save Genotype for Replicates', 
     'type': 'integer',
     'description': ''' *** Save Genotype for Replicates ***
        | Optional output of genotype information. This option is turned off by
        default (0) because this format is not efficient in storing a small number
        of mutants. If specified by a positive number n, genotype
        in standard .ped format for the first n replicates will be
        outputted to n files. 
        |For example.
        | 1 -- genotype of the first replicate will be saved to file.
        | 3 -- genotype of the first three replicates will be outputted to
        |        three files.
        |In particular, if specified as -1, genotype information of ALL simulation
        replicates will be saved. 
        ''',
     'validator': 'type(saveGenotype) == int and saveGenotype in range(-1,numReps+1)',
    },
    {'name': 'saveStat',
     'default': 0,
     'label': '    Save Statistics for Replicates',
     'type': 'integer',
     'description': ''' *** Save Statistics for Replicates ***
        | This optional parameter (default None) may output statistics to files.
        It should be specified in the same manner as 'Save Genotype for Replicates' requires.''',
    'validator': 'type(saveStat) == int and saveStat in range(-1,numReps+1)',
    },
    {'name': 'variantPool',
     'default': False,
     'label': '    Output variant data pool',
     'type': bool,
     'description': ''' Output variant data pool in addition to site-frequency spectrum (sfs) '''
    }
]


def testProgressBarGUI():
    try:
        progress = ProgressBar("", 2)
        for i in range(2):
            progress.update(i+1)
        progress.done()
        return True
    except:
        return False
    

class NumSegregationSites(sim.PyOperator):
    '''A Python operator to count the number of segregation sites (number of
    distinct mutants), average number of segreagation sites of individuals,
    and average allele frequency of these mutants. The results are saved
    in variables ``numSites``, ``avgSites`` and ``avgFreq``.
    '''
    def __init__(self, *args, **kwargs):
        sim.PyOperator.__init__(self, func=self.countSites, *args, **kwargs)

    def countSites(self, pop):
        '''Count the number of segregation sites, average sites per individual,
        average allele frequency.'''
        revertFixedSites(pop)
        geno = pop.genotype()
        numMutants = float(len(geno) - geno.count(0)) 
        numSites = len(set(geno)) - 1
        if numMutants == 0:
            avgFreq = 0
        else:
            avgFreq = numMutants / numSites / (2*pop.popSize())
        pop.dvars().numSites = numSites
        pop.dvars().avgSites = float(numMutants) / pop.popSize()
        pop.dvars().avgFreq = avgFreq
        return True

def mutantsToAlleles(pop, logger):
    '''Convert a population from mutational space to allele space. Monomorphic
    markers are ignored.
    '''
    # figure out chromosomes and markers
    markers = {}
    for ch,region in enumerate(pop.chromNames()):
        chNumber = region.split(':')[0][3:]
        loci = set()
        for ind in pop.individuals():
            loci |= set(ind.genotype(0, ch))
            loci |= set(ind.genotype(1, ch))
        if markers.has_key(chNumber):
            markers[chNumber] |= loci
        else:
            markers[chNumber] = loci
    # create a population for each chromosome
    pops = []
    chroms = markers.keys()
    chroms.sort()
    for ch in chroms:
        markers[ch] = list(markers[ch])
        markers[ch].remove(0)
        markers[ch].sort()
        if logger:
            logger.info('Chromosome %s has %d markers' % (ch, len(markers[ch])))
        apop = sim.Population(pop.popSize(), loci=len(markers[ch]),
            lociPos=markers[ch])
        # get a dictionary of loci position
        lociPos = {}
        for idx,loc in enumerate(apop.lociPos()):
            lociPos[loc] = idx
        for aind,mind in zip(apop.individuals(), pop.individuals()):
            for p in range(2):
                for mutant in mind.genotype(p):
                    if mutant != 0:
                        aind.setAllele(1, lociPos[mutant], p)
        pops.append(apop)
    for pop in pops[1:]:
        pops[0].addChromFrom(pop)
    return pops[0]


def allelesToMutants(pop, regions, logger=None):
    '''Convert a population from allele space to mutational space, using
    specified regions.
    '''
    pops = []
    for region in regions:
        loci = []
        ch_name = region.split(':')[0][3:]
        start, end = [int(x) for x in region.split(':')[1].split('..')]
        try:
            ch = pop.chromByName(ch_name)
        except:
            raise ValueError('Chromosome %s is not available in passed population.' % ch_name)
        for loc in range(pop.chromBegin(ch), pop.chromEnd(ch)):
            pos = pop.locusPos(loc)
            if pos >= start and pos <= end:
                loci.append(loc)
        # get the mutants for each individual
        allAlleles = []
        for ind in pop.individuals():
            alleles0 = []
            alleles1 = []
            for loc in loci:
                if ind.allele(loc, 0) != 0:
                    alleles0.append(int(pop.locusPos(loc)))
                if ind.allele(loc, 1) != 0:
                    alleles1.append(int(pop.locusPos(loc)))
            allAlleles.extend([alleles0, alleles1])
        # maximum number of mutants
        maxMutants = max([len(x) for x in allAlleles])
        if logger is not None:
            logger.info('%d loci are identified with at most %d mutants in region %s.' % (len(loci), maxMutants, region))
        # create a population
        mpop = sim.Population(pop.popSize(), loci=maxMutants, chromNames=region)
        # put in mutants
        for idx,ind in enumerate(mpop.individuals()):
            geno = ind.genotype(0)
            for loc,mut in enumerate(allAlleles[idx*2]):
                geno[loc] = mut
            geno = ind.genotype(1)
            for loc,mut in enumerate(allAlleles[idx*2+1]):
                geno[loc] = mut
        pops.append(mpop)
    # merge all populations into one
    for pop in pops[1:]:
        pops[0].addChromFrom(pop)
    return pops[0]

def addMutantsFrom(pop, param):
    # Adding mutants
    extMutantFile, regions, logger = param
    mPop = sim.loadPopulation(extMutantFile)
    # convert allele-based population to mutation based population.
    mPop = allelesToMutants(mPop, regions, logger)
    #
    mPop.resize(pop.popSize())
    # Add loci to pop
    for ch in range(mPop.numChrom()):
        pop.addLoci([ch]*mPop.numLoci(ch), range(pop.numLoci(ch) + 1,
            pop.numLoci(ch) + mPop.numLoci(ch) + 1))
    if logger:
        # if an initial population is given
        logger.info('Adding mutants to population after bottleneck')
    # Add mutants to pop
    for ind, mInd in zip(pop.individuals(), mPop.individuals()):
        for p in range(2):
            for ch in range(pop.numChrom()):
                geno = ind.genotype(p, ch)
                mGeno = mInd.genotype(p, ch)
                idx = geno.index(0)
                for i,m in enumerate(mGeno):
                    if m == 0:
                        break
                    geno[idx + i] = m
    return True


###
### The container.Counter class only exist in Python 2.7 so I put it here
###
from operator import itemgetter
from heapq import nlargest
from itertools import repeat, ifilter

class Counter(dict):
    '''Dict subclass for counting hashable objects.  Sometimes called a bag
    or multiset.  Elements are stored as dictionary keys and their counts
    are stored as dictionary values.

    >>> Counter('zyzygy')
    Counter({'y': 3, 'z': 2, 'g': 1})

    '''

    def __init__(self, iterable=None, **kwds):
        '''Create a new, empty Counter object.  And if given, count elements
        from an input iterable.  Or, initialize the count from another mapping
        of elements to their counts.

        >>> c = Counter()                           # a new, empty counter
        >>> c = Counter('gallahad')                 # a new counter from an iterable
        >>> c = Counter({'a': 4, 'b': 2})           # a new counter from a mapping
        >>> c = Counter(a=4, b=2)                   # a new counter from keyword args

        '''        
        self.update(iterable, **kwds)

    def __missing__(self, key):
        return 0


    def elements(self):
        '''Iterator over elements repeating each as many times as its count.

        >>> c = Counter('ABCABC')
        >>> sorted(c.elements())
        ['A', 'A', 'B', 'B', 'C', 'C']

        If an element's count has been set to zero or is a negative number,
        elements() will ignore it.

        '''
        for elem, count in self.iteritems():
            for _ in repeat(None, count):
                yield elem

    # Override dict methods where the meaning changes for Counter objects.

    @classmethod
    def fromkeys(cls, iterable, v=None):
        raise NotImplementedError(
            'Counter.fromkeys() is undefined.  Use Counter(iterable) instead.')

    def update(self, iterable=None, **kwds):
        '''Like dict.update() but add counts instead of replacing them.

        Source can be an iterable, a dictionary, or another Counter instance.

        >>> c = Counter('which')
        >>> c.update('witch')           # add elements from another iterable
        >>> d = Counter('watch')
        >>> c.update(d)                 # add elements from another counter
        >>> c['h']                      # four 'h' in which, witch, and watch
        4

        '''        
        if iterable is not None:
            if hasattr(iterable, 'iteritems'):
                if self:
                    self_get = self.get
                    for elem, count in iterable.iteritems():
                        self[elem] = self_get(elem, 0) + count
                else:
                    dict.update(self, iterable) # fast path when counter is empty
            else:
                self_get = self.get
                for elem in iterable:
                    self[elem] = self_get(elem, 0) + 1
        if kwds:
            self.update(kwds)

    def __delitem__(self, elem):
        'Like dict.__delitem__() but does not raise KeyError for missing values.'
        if elem in self:
            dict.__delitem__(self, elem)

    def __repr__(self):
        if not self:
            return '%s()' % self.__class__.__name__
        items = ', '.join(map('%r: %r'.__mod__, self.most_common()))
        return '%s({%s})' % (self.__class__.__name__, items)
 
#
# End of copied code
#


#def saveMarkerInfoToFile(pop, filename, logger=None):
#    '''Save a map file with an additional column of allele frequency. The
#    population has to be in mutational space. This function assumes that
#    there is a variable selCoef in this population which contains selection
#    coefficients for all mutants.
#    '''
#    allCounts = [Counter() for x in range(pop.numChrom())]
#    prog = ProgressBar('Counting number of mutants', pop.popSize(), gui=testProgressBarGUI())
#    for ind in pop.individuals():
#        # there can be memory problem....
#        for ch in range(pop.numChrom()):
#            allCounts[ch].update(ind.genotype(0, ch))
#            allCounts[ch].update(ind.genotype(1, ch))
#        prog.update()
#    allMutants = []
#    selCoef = pop.dvars().selCoef
#    if filename:
#        map = open(filename, 'w')
#        print >> map, 'name\tchrom\tposition\tfrequency\ts\th'
#    for ch,region in enumerate(pop.chromNames()):
#        # real chromosome number
#        chName = region.split(':')[0][3:]
#        counts = allCounts[ch]
#        # get markers
#        mutants = counts.keys()
#        mutants.sort()
#        # allele 0 is fake
#        if mutants[0] == 0:
#            mutants = mutants[1:]
#        allMutants.append(mutants)
#        if filename:
#            # find all markers
#            sz = pop.popSize() * 2.
#            for idx,marker in enumerate(mutants):
#                if type(selCoef) == type({}):
#                    print >> map, 'loc%d_%d\t%s\t%d\t%.8f\t%.8f\t%.3f' % (ch + 1, idx + 1, chName, marker,
#                        counts[marker] / sz, selCoef[marker][0], selCoef[marker][1])
#                else:
#                    print >> map, 'loc%d_%d\t%s\t%d\t%.8f\t%.8f\t%.3f' % (ch + 1, idx + 1, chName, marker,
#                        counts[marker] / sz, selCoef, 0.5)
#    if filename:
#        map.close()
#    return allMutants
        

def saveMarkerInfoToFile(pop, fileName, regInt, replicate, logger=None):
    '''Save a map file with an additional column of allele frequency. The
    population has to be in mutational space. This function assumes that
    there is a variable selCoef in this population which contains selection
    coefficients for all mutants.
    '''
    allCounts = [Counter() for x in range(pop.numChrom())]
    prog = ProgressBar('Counting number of mutants for replicate %d' % replicate, pop.popSize(), gui=testProgressBarGUI())
    for ind in pop.individuals():
        # there can be memory problem....
        for ch in range(pop.numChrom()):
            allCounts[ch].update(ind.genotype(0, ch))
            allCounts[ch].update(ind.genotype(1, ch))
        prog.update()
    allMutants = []
    selCoefficient = pop.dvars().selCoef
    outFile = open(fileName+'.sfs', 'a')
    # write gene length to *.sfs file
    print >> outFile, '# Replicate #%d gene length = %d' % (replicate, regInt)
    maf, sel, pos, vaf = [],[],[],[]
    # write maf, sel and pos info into *.sfs file
    for ch,region in enumerate(pop.chromNames()):
        # real chromosome number
        chName = region.split(':')[0][3:]
        counts = allCounts[ch]
        # get markers
        mutants = counts.keys()
        mutants.sort()
        # allele 0 is fake
        if mutants[0] == 0:
            mutants = mutants[1:]
        allMutants.append(mutants)
        # write to file
        sz = pop.popSize() * 2.
        for idx2, marker in enumerate(mutants):
            # vaf - variant allele frequency
            # maf - minor allele frequency
            vaf_marker = counts[marker] / sz
            maf_marker = vaf_marker if vaf_marker <= 0.5 else 1-vaf_marker
            print >> outFile, ' '.join([('R'+str(replicate)) if replicate>=1 else fileName, '%s' % chName+'-'+str(replicate), '%d' % marker, '%.8f' % maf_marker, '%.8f' % selCoefficient[marker][0]])
            maf.append(round(maf_marker, 8))
            sel.append(round(selCoefficient[marker][0], 8))
            pos.append(int(marker))
            vaf.append(round(vaf_marker, 8))
    outFile.close()    
    return allMutants, maf, sel, pos, vaf


def saveMutantsToFile(pop, filename, infoFields=[], logger=None):
    '''Save haplotypes as a list of mutant locations to file, in the format of
       ind_idx reg_id FIELDS mut1 mut2 ...
    where FIELDS are information fields.
    '''
    mut = open(filename, 'w')
    prog = ProgressBar('Writing mutants of %d individuals to %s' % (pop.popSize(), filename), pop.popSize(), gui=testProgressBarGUI())
    for idx,ind in enumerate(pop.allIndividuals()):
        fields = ' '.join([str(ind.info(x)) for x in infoFields])
        for ch in range(pop.numChrom()):
            geno = list(ind.genotype(0, ch))
            geno.sort()
            print >> mut, idx+1, fields, ' '.join([str(x) for x in geno if x != 0])
            geno = list(ind.genotype(1, ch))
            geno.sort()
            if geno[0] == 0:
                geno = geno[1:]
            print >> mut, idx+1, fields, ' '.join([str(x) for x in geno if x != 0])
        prog.update()
    mut.close()

def saveGenotypeToFile(pop, filename, allMutants, logger=None):
    '''Save genotype in .ped file format. Because there is no family structure, we have
        famid = 1, 2, 3, ...
        id = 1
        fa = 0
        ma = 0
        sex = 1 for male and 2 for female
        aff = 1 for unaffected and 2 for affected
        genotype 

    allMutants:
        lists of mutants returned by function markerFile
    '''
    if filename != '':
        if logger:
            logger.info('Saving genotype to %s in standard .ped format.' % filename)
        ped = open(filename, 'w')
    # marker index...
    markerPos = []
    for mutants in allMutants:
        pos = {}
        for idx,m in enumerate(mutants):
            pos[m] = idx
        markerPos.append(pos)
    if filename != '':
        prog = ProgressBar('Writing genotype of %d individuals to %s' % (pop.popSize(), filename), pop.popSize(), gui=testProgressBarGUI())
    #prog = ProgressBar('Writing genotype of %d individuals to %s' % (pop.popSize(), filename), pop.popSize(), gui=False)
    sexCode = {sim.MALE: 1, sim.FEMALE: 2}
    affCode = {False: 1, True: 2}
    genos = []
    for cnt, ind in enumerate(pop.individuals()):
        if filename != '':
            print >> ped, '%s 0 0 0 %d %d' % (cnt + 1, sexCode[ind.sex()], affCode[ind.affected()]),
        for ch in range(pop.numChrom()):
            # a blank genotype
            geno = [0]*(len(markerPos[ch])*2)
            # add 1 according to mutant location (first ploidy)
            for m in ind.genotype(0, ch):
                if m == 0:
                    break
                geno[2*markerPos[ch][m]] = 1
            # add 1 according to mutant location (second ploidy)
            for m in ind.genotype(1, ch):
                if m == 0:
                    break
                geno[2*markerPos[ch][m]+1] = 1
            genos.append(geno)
            if filename != '':
                print >> ped, ' '.join([str(x) for x in geno]),
        if filename != '':        
            print >> ped
            prog.update()
    if filename != '':
        ped.close()
    return genos

class fitnessCollector:
    '''This is a simple connection class that gets output from 
    a InfSiteSelector and collect mutant fitness'''
    def __init__(self):
        self.selCoef = {}

    def getCoef(self, lines):
        for line in lines.strip().split('\n'):
            mut, sel, h = line.split()
            self.selCoef[int(mut)] = float(sel), float(h)


#def mixedGamma(selCoef):
#    '''This function returns a random fitness value for a new mutant
#    according to a mixed_gamma distribution. If a parameter loc is defined,
#    locus index will be passed so that you can return different selection
#    coefficient for different locations.
#    '''
#    if len(selCoef) == 5:
#        selCoef = list(selCoef) + [0.00001, 0.1]
#    if len(selCoef) != 7:
#        raise ValueError("A list of five or seven parameters is needed.")
#    def func():
#        if sim.getRNG().randUniform() < selCoef[0]:
#            return selCoef[1], selCoef[4]
#        while True:
#            s = sim.getRNG().randGamma(selCoef[2], selCoef[3])
#            if s > selCoef[5] and s < selCoef[6]:
#                return s, selCoef[4]
#    return func


def mixedGamma5(selCoef, selRange):
    '''
    selCoef : [p,s,k,d,h], binned at [selRange[0], selRange[1]]
    '''
    def func():
        if sim.getRNG().randUniform() < selCoef[0]:
            return selCoef[1], selCoef[4]
        while True:
            s = sim.getRNG().randGamma(selCoef[2], selCoef[3])
            if s < selRange[0]:
                return selRange[0], selCoef[4]
            elif s > selRange[1]:
                return selRange[1], selCoef[4]
            else:
                return s, selCoef[4]
    #
    return func


def mixedGamma7(selCoef, selRange):
    '''
    selCoef : [p,s,k,d,h,l,u], truncated at [l, u], binned at [selRange[0], selRange[1]]
    '''
    def func():
        if sim.getRNG().randUniform() < selCoef[0]:
            return selCoef[1], selCoef[4]
        while True:
            s = sim.getRNG().randGamma(selCoef[2], selCoef[3])
            if selCoef[5] < s < selCoef[6]:
                if s < selRange[0]:
                    return selRange[0], selCoef[4]
                elif s > selRange[1]:
                    return selRange[1], selCoef[4]
                else:
                    return s, selCoef[4]
    #
    return func


def complexMixedGamma8(selCoef, selRange):
    '''
    selCoef : [p,s,q,k1,d1,k2,d2,h], where p is the probability of having
        the selection coefficient equal to s; q is the probability of
        having the selection coefficient following a positive gamma
        distribution with shape/scale parameters k1/d1, therefore, the probability of having selection coefficient following a negative/opposite gamma distribution is 1-p-q. The negative gamma distribution takes parameters k2 and d2. Note that the generated selection coefficient for the negative gamma distribution will be returned as its opposite number. h is the dominance coefficient (h=0.5 by default)
    By default truncate at both [selRange[0], selRange[1]] and [-selRange[1], -selRange[0]]
    '''
    def func():
        randNum = sim.getRNG().randUniform()
        if randNum < selCoef[0]:
            return selCoef[1], selCoef[7]
        elif randNum < selCoef[0] + selCoef[2]:
            while True:
                s = sim.getRNG().randGamma(selCoef[3], selCoef[4])
                if s < selRange[0]:
                    return selRange[0], selCoef[7]
                elif s > selRange[1]:
                    return selRange[1], selCoef[7]
                else:
                    return s, selCoef[7]
        else:
            while True:
                s = sim.getRNG().randGamma(selCoef[5], selCoef[6])
                if s < selRange[0]:
                    return -selRange[0], selCoef[7]
                elif s > selRange[1]:
                    return -selRange[1], selCoef[7]
                else:
                    return -s, selCoef[7]
    #
    return func
    

def complexMixedGamma12(selCoef, selRange):
    '''
    [p,s,q,k1,d1,k2,d2,h,l1,u1,l2,u2]
    '''
    def func():
        randNum = sim.getRNG().randUniform()
        if randNum < selCoef[0]:
            return selCoef[1], selCoef[7]
        elif randNum < selCoef[0] + selCoef[2]:
            while True:
                s = sim.getRNG().randGamma(selCoef[3], selCoef[4])
                if selCoef[8] < s < selCoef[9]:
                    if s < selRange[0]:
                        return selRange[0], selCoef[7]
                    elif s > selRange[1]:
                        return selRange[1], selCoef[7]
                    else:
                        return s, selCoef[7]
        else:
            while True:
                s = sim.getRNG().randGamma(selCoef[5], selCoef[6])
                if selCoef[10] < s < selCoef[11]:
                    if s < selRange[0]:
                        return -selRange[0], selCoef[7]
                    elif s > selRange[1]:
                        return -selRange[1], selCoef[7]
                    else:
                        return -s, selCoef[7]
    #
    return func

def genSelDistFunc(selCoef, selRange):
    '''
    Return a python operator (loadable by simuPOP.sandbox.MutSpaceSelector(selDist=...) function)to generate selection coefficient distribution according to the given 'selCoef' while choosing model according to the length of 'selCoef'
    len(selCoef) == 2 : constant selection coefficient [s, h], where s is the selection coefficient and h is the dominance coefficient (h=0.5 by default)
    len(selCoef) == 3 : gamma distributed selection coefficient [k, d, h], where k is the shape parameter and d is the scale parameter
    len(selCoef) == 5 : mixed gamma distributed selection coefficient [p, s, k, d, h], where p is the probability of having the selection coefficient equal to s; k, d are shape and scale parameters of gamma distribution; h is the dominance coefficient
    len(selCoef) == 7 : truncated mixed gamma [p,s,k,d,h,l,u], where l,u are lower and upper bounds
    len(selCoef) == 8 : complex mixed gamma distribution that can generate a mix of constant, positive gamma and negative gamma distributions. The negative distribution represents protective variant sites with negative selection coefficients. [p,s,q,k1,d1,k2,d2,h]. See func 'complexMixedGamma8()'
    len(selCoef) == 12: truncated complex mixed gamma distribution, [p,s,q,k1,d1,k2,d2,h,l1,u1,l2,u2]
    '''
    if len(selCoef) == 2:
        return [sim.CONSTANT] + selCoef
    elif len(selCoef) == 3:
        return [sim.GAMMA_DISTRIBUTION] + selCoef
    elif len(selCoef) == 5:
        return mixedGamma5(selCoef, selRange)
    elif len(selCoef) == 7:
        return mixedGamma7(selCoef, selRange)
    elif len(selCoef) == 8:
        return complexMixedGamma8(selCoef, selRange)
    elif len(selCoef) == 12:
        return complexMixedGamma12(selCoef, selRange)
    else:
        raise ValueError("Wrong input of selection coefficient distribution model or " \
                         + "customized selection coefficient")
        
    

def multiStageDemoFunc(N, G, splitTo, splitAt):
    '''Return a demographic function with specified parameter
    '''
    # the demographic model: N[0] = the population size of the burnin generation
    # 0,    G[0], G[0] + G[1], ..., reflexting
    # N[0], N[1], N[2], ....
    Gens = [sum(G[:i]) for i in range(len(G)+1)]
    #
    def demoFunc(gen, pop):
        if len(splitTo) > 1 and gen == splitAt:
            pop.splitSubPop(0, splitTo)
        nSP = pop.numSubPop()
        # default
        sz = N[-1]
        for i in range(len(G)):
            if Gens[i] <= gen < Gens[i+1]:
                # at constant or any bottleneck stage
                if N[i] >= N[i+1]:
                    sz = N[i+1]
                # at any expansion stage
                else:
                    # to make sure that the last generation of this expansion stage 
                    # has the exact required number of individuals.
                    if gen == Gens[i+1] - 1:
                        sz = N[i+1]
                    else:
                        r = math.log(N[i+1] * 1.0 / N[i]) / G[i]
                        sz = int(N[i] * math.exp(r*(gen - Gens[i])))
                break
        # because population might be split, ..
        if nSP == 1:
            return sz
        else:
            # split with proportion
            sz1 = [int(sz*x) for x in splitTo]
            # remenders are given to the last subpopulation
            sz1[-1] += sz - sum(sz1)
            return sz1
    return demoFunc

def simuRareVariants(regions, N, G, mu, revertFixedSites, selDist, selCoef,
                     selModel='additive', selRange=[0.0,1.0], recRate=0, 
        splitTo=[1], splitAt=0, migrRate=0, steps=[100], mutationModel='finite_sites',
        initPop='', extMutantFile='', addMutantsAt=0,
        statFile='', popFile='', markerFile='', mutantFile='', genotypeFile='',
        verbose=1, logger=None, variantPool=False, regInt=1500, replicate=1):
    '''
    Please refer to simuRareVariants.py -h for a detailed description of all parameters.
    Note that a user-defined function can be passed to parameter selDist to specify
    arbitrary distribution of fitness.
    '''
    #
    # convert regions to start/end positions
    ranges = []
    for region in regions:
        start, end = [int(x) for x in region.split(':')[1].split('..')]
        ranges.append((start, end+1))
    if logger:
        logger.info('%s regions with a total length of %d basepair.' % (len(ranges), sum([x[1]-x[0] for x in ranges])))
    #
    # set default parameter
    if selCoef == []:
        # set default parameters (for ESP-related analysis, assuming 37% neutral/synonymous mutations)
        if selDist == 'Kyrukov_2009_European':
            # pgamma(10E-5, shape=0.562341, scale=0.01) is about 8% 
            selCoef = [0.37, 0.0, 0.562341, 0.01, 0.5]
        elif selDist == 'Eyre-Walker_2006':
            #selCoef = [0.23, 0.185*2, 0.5]
            selCoef = [0.37, 0.0, 0.23, 0.185*2, 0.5]
        elif selDist == 'Boyko_2008_African':
            #selCoef = [0.184, 0.160*2, 0.5]
            selCoef = [0.37, 0.0, 0.184, 0.160*2, 0.5]
        elif selDist == 'Boyko_2008_European':
            #selCoef = [0.206, 0.146*2, 0.5]
            selCoef = [0.37, 0.0, 0.206, 0.146*2, 0.5]
        elif selDist == 'constant':
            selCoef = [0.01, 0.5]
        elif not callable(selDist):
            raise ValueError("Unsupported random distribution")
    else:
        selCoef = selCoef
    # 
    if len(steps) == 0:
        # at the end of each stage
        steps = G
    elif len(steps) == 1:
        # save step for each stage
        steps = steps * len(G)
    # use a right selection operator.
    collector = fitnessCollector()
    mode = {'multiplicative': sim.MULTIPLICATIVE,
        'additive': sim.ADDITIVE,
        'exponential': sim.EXPONENTIAL}[selModel]
    #
    if type(popFile) == str:
        popFile = [popFile, -1]
    #
    if callable(selDist):
        mySelector = MutSpaceSelector(selDist=selDist, mode=mode, output=collector.getCoef)
    else:
        mySelector = MutSpaceSelector(selDist=genSelDistFunc(selCoef, selRange),
                                      mode=mode, output=collector.getCoef)
    #
    # Evolve
    if os.path.isfile(initPop):
        if logger:
            logger.info('Loading initial population %s...' % initPop)
        pop = sim.loadPopulation(initPop)
        if pop.numChrom() != len(regions):
            raise ValueError('Initial population %s does not have specified regions.' % initPop)
        for ch,reg in enumerate(regions):
            if pop.chromName(ch) != reg:
                raise ValueError('Initial population %s does not have region %s' % (initPop, reg))
        pop.addInfoFields(['fitness', 'migrate_to'])
    else:
        pop = sim.Population(size=N[0], loci=[10]*len(regions), chromNames=regions,
            infoFields=['fitness', 'migrate_to'])
    if logger:
        startTime = time.clock()
    #
    progGen = []
    # 0, G[0], G[0]+G[1], ..., sum(G)
    Gens = [sum(G[:i]) for i in range(len(G)+1)]
    for i in range(len(Gens)-1):
        progGen += range(Gens[i], Gens[i+1], steps[i])
    # if 'revertFixedSites is True', revert alleles at fixed loci to wildtype
    if revertFixedSites:
        pop.evolve(
            initOps=sim.InitSex(),
            preOps=
            #[
    #            sim.PyOutput('''Statistics outputted are
    #1. Generation number,
    #2. population size (a list),
    #3. number of segregation sites,
    #4. average number of segregation sites per individual
    #5. average allele frequency * 100
    #6. average fitness value
    #7. minimal fitness value of the parental population
    #''', at = 0)] + \
                [sim.IfElse(verbose >=0, ifOps=[sim.PyOutput('Starting stage %d\n' % i, at = Gens[i]) for i in range(0, len(Gens))])] + \
                # add alleles from an existing population 
                [sim.IfElse(extMutantFile != '',
                    ifOps = [
                        sim.PyOutput('Loading and converting population %s' % extMutantFile),
                        sim.PyOperator(func=addMutantsFrom, param=(extMutantFile, regions, logger)),
                    ], at = addMutantsAt),
                # revert alleles at fixed loci to wildtype
                RevertFixedSites(),
                # mutate in a region at rate mu, if verbose > 2, save mutation events to a file
                MutSpaceMutator(mu, ranges, {'finite_sites':1, 'infinite_sites':2}[mutationModel],
                    output='' if verbose < 2 else '>>mutations.lst'),
                # selection on all loci
                mySelector,
                # output statistics in verbose mode
                # output stat to screen
                sim.IfElse(verbose > 0, ifOps=[
                    sim.Stat(popSize=True, meanOfInfo='fitness', minOfInfo='fitness'),
                    NumSegregationSites(),
                    sim.PyEval(r'"%5d %s %5d %.6f %.6f %.6f %.6f\n" '
                        '% (gen, subPopSize, numSites, avgSites, avgFreq*100, meanOfInfo["fitness"], minOfInfo["fitness"])'
                        ),
                    ], at = progGen
                ),
                # output stat to file
                sim.IfElse(statFile!='', ifOps=[
                    sim.Stat(popSize=True, meanOfInfo='fitness', minOfInfo='fitness'),
                    NumSegregationSites(),
                    sim.PyEval(r'"%5d %s %5d %.6f %.6f %.6f %.6f\n" '
                        '% (gen, subPopSize, numSites, avgSites, avgFreq*100, meanOfInfo["fitness"], minOfInfo["fitness"])',
                        output='>>' + statFile),
                    ], at = progGen
                ),
                sim.IfElse(len(splitTo) > 1,
                    sim.Migrator(rate=migrIslandRates(migrRate, len(splitTo)),
                        begin=splitAt + 1)
                ),
            ],
            matingScheme=sim.RandomMating(ops=MutSpaceRecombinator(recRate, ranges),
                subPopSize=multiStageDemoFunc(N, G, splitTo, splitAt)),
            postOps = sim.SavePopulation(popFile[0], at=popFile[1]),
            finalOps=[
                # revert fixed sites so that the final population does not have fixed sites
                RevertFixedSites(),
                sim.IfElse(verbose > 0, ifOps=[
                    # statistics after evolution
                    sim.Stat(popSize=True),
                    NumSegregationSites(),
                    sim.PyEval(r'"%5d %s %5d %.6f %.6f %.6f %.6f\n" '
                        '% (gen+1, subPopSize, numSites, avgSites, avgFreq*100, meanOfInfo["fitness"], minOfInfo["fitness"])',
                        output='>>' + statFile),
                    sim.PyEval(r'"Simulated population has %d individuals, %d segregation sites.'
                               r'There are on average %.1f sites per individual. Mean allele frequency is %.4f%%.\n"'
                               r'% (popSize, numSites, avgSites, avgFreq*100)'),
                ]),
            ],
            gen = Gens[-1]
        )
    # if 'revertFixedSites is False':
    else:
        pop.evolve(
            initOps=sim.InitSex(),
            preOps=
            #[
    #            sim.PyOutput('''Statistics outputted are
    #1. Generation number,
    #2. population size (a list),
    #3. number of segregation sites,
    #4. average number of segregation sites per individual
    #5. average allele frequency * 100
    #6. average fitness value
    #7. minimal fitness value of the parental population
    #''', at = 0)] + \
                [sim.IfElse(verbose >=0, ifOps=[sim.PyOutput('Starting stage %d\n' % i, at = Gens[i]) for i in range(0, len(Gens))])] + \
                # add alleles from an existing population 
                [sim.IfElse(extMutantFile != '',
                    ifOps = [
                        sim.PyOutput('Loading and converting population %s' % extMutantFile),
                        sim.PyOperator(func=addMutantsFrom, param=(extMutantFile, regions, logger)),
                    ], at = addMutantsAt),
                # RevertFixedSites(),
                # mutate in a region at rate mu, if verbose > 2, save mutation events to a file
                MutSpaceMutator(mu, ranges, {'finite_sites':1, 'infinite_sites':2}[mutationModel],
                    output='' if verbose < 2 else '>>mutations.lst'),
                # selection on all loci
                mySelector,
                # output statistics in verbose mode
                # output stat to screen
                sim.IfElse(verbose > 0, ifOps=[
                    sim.Stat(popSize=True, meanOfInfo='fitness', minOfInfo='fitness'),
                    NumSegregationSites(),
                    sim.PyEval(r'"%5d %s %5d %.6f %.6f %.6f %.6f\n" '
                        '% (gen, subPopSize, numSites, avgSites, avgFreq*100, meanOfInfo["fitness"], minOfInfo["fitness"])'
                        ),
                    ], at = progGen
                ),
                # output stat to file
                sim.IfElse(statFile!='', ifOps=[
                    sim.Stat(popSize=True, meanOfInfo='fitness', minOfInfo='fitness'),
                    NumSegregationSites(),
                    sim.PyEval(r'"%5d %s %5d %.6f %.6f %.6f %.6f\n" '
                        '% (gen, subPopSize, numSites, avgSites, avgFreq*100, meanOfInfo["fitness"], minOfInfo["fitness"])',
                        output='>>' + statFile),
                    ], at = progGen
                ),
                sim.IfElse(len(splitTo) > 1,
                    sim.Migrator(rate=migrIslandRates(migrRate, len(splitTo)),
                        begin=splitAt + 1)
                ),
            ],
            matingScheme=sim.RandomMating(ops=MutSpaceRecombinator(recRate, ranges),
                subPopSize=multiStageDemoFunc(N, G, splitTo, splitAt)),
            postOps = sim.SavePopulation(popFile[0], at=popFile[1]),
            finalOps=[
                #RevertFixedSites(),
                sim.IfElse(verbose > 0, ifOps=[
                    # statistics after evolution
                    sim.Stat(popSize=True),
                    NumSegregationSites(),
                    sim.PyEval(r'"%5d %s %5d %.6f %.6f %.6f %.6f\n" '
                        '% (gen+1, subPopSize, numSites, avgSites, avgFreq*100, meanOfInfo["fitness"], minOfInfo["fitness"])',
                        output='>>' + statFile),
                    sim.PyEval(r'"Simulated population has %d individuals, %d segregation sites.'
                               r'There are on average %.1f sites per individual. Mean allele frequency is %.4f%%.\n"'
                               r'% (popSize, numSites, avgSites, avgFreq*100)'),
                ]),
            ],
            gen = Gens[-1]
        )
    # end {if-else: revertFixedSites}
    # record selection coefficients to population
    if len(collector.selCoef) == 0:
        # this must be the neutral case where a NonOp has been used.
        pop.dvars().selCoef = 0
    else:
        pop.dvars().selCoef = collector.selCoef
    #
    if logger:
        logger.info('Population simulation takes %.2f seconds' % (time.clock() - startTime))
    if logger:
        logger.info('Saving marker information to file %s' % markerFile)
    # write mutants info to *.sfs file
    mutants, maf, sel, pos, vaf = saveMarkerInfoToFile(pop, markerFile, regInt, replicate, logger)   
    genos = None
    #if variantPool or genotypeFile:
    #    if logger:
    #        logger.info('Saving genotype in .ped format to file %s' % genotypeFile)
    #genos = saveGenotypeToFile(pop, genotypeFile, mutants, logger)
    #if mutantFile:
    #    if logger:
    #        logger.info('Saving mutants to file %s' % mutantFile)
    #    saveMutantsToFile(pop, mutantFile, logger=logger)
    return pop, genos, maf, sel, pos, vaf


def srvOutput(regRange, fileName, numReps, N, G, mu, revertFixedSites, selDist, selCoef,
              selModel, selRange, recRate, steps, mutationModel, verbose,
              saveGenotype=0, saveStat=0, variantPool=False
   # initPop='', extMutantFile='', addMutantsAt=0, splitTo=[1], splitAt=0, migrRate=0,
   # statFile='', popFile='', markerFile='', mutantFile='', genotypeFile='',
   # verbose=1, logger=None
    ):
    '''
    '''
    # write the following to fileName.sfs, gene length, mafs, sels and pos info
    #
    outFile = open(fileName+'.sfs', 'w')
    print >> outFile, '#name chr position maf annotation'
    outFile.close()
    #
    # check if need to output genotype and statistics to files
    if saveGenotype == -1:
        saveGenoNum = range(1, numReps+1)
    else:
        saveGenoNum = range(1, saveGenotype+1)
    if saveStat == -1:
        saveStatNum = range(1, numReps+1)
    else:
        saveStatNum = range(1, saveStat+1)
    #
    dicSaveGeno = {}
    dicSaveStat = {}
    for i in range(1, numReps+1):
        if i in saveGenoNum:
            dicSaveGeno[i] = fileName+'_rep_'+str(i)+'.ped'
        else:
            dicSaveGeno[i] = ''
        if i in saveStatNum:
            dicSaveStat[i] = fileName+'_rep_'+str(i)+'.stat'
        else:
            dicSaveStat[i] = ''
    #
    startTime = [0]
    endTime = [0]
    dictGenos = {}
    # create a temporary folder
    if variantPool:
        tempFolder = tempfile.mkdtemp()
    # run for multiple replicates
    for idx, num in enumerate(range(1, numReps+1)):
        random.seed(time.time())
        startTime.append(time.clock())
        regInt = random.randint(regRange[0], regRange[1])
        regions = ['chr1:1..'+ str(regInt)]
        #
        if verbose in [0,1]:
            print 'Begin to simulate replicate #', num
            print 'Gene length of current replicate = ', regInt
        #
        if verbose == 1 and num == 1:
            print('''Statistics outputted are
1. Generation number,
2. population size (a list),
3. number of segregation sites,
4. average number of segregation sites per individual
5. average allele frequency * 100
6. average fitness value
7. minimal fitness value of the parental population
                ''')
        pop, genos, maf, sel, pos, vaf = simuRareVariants(regions=regions, N=N, G=G, mu=mu, revertFixedSites=revertFixedSites, selDist=selDist,
                               selCoef=selCoef, selModel=selModel, selRange=selRange, recRate=recRate,
                               steps=steps, mutationModel=mutationModel, verbose=verbose,
                               genotypeFile=dicSaveGeno[num], statFile=dicSaveStat[num], variantPool=variantPool, regInt=regInt, replicate=idx+1, markerFile=fileName)
        
        
        #if variantPool:
        #    cwd = os.getcwd()
        #    os.chdir(tempFolder)
        #    haplotypes = convertGenosToListOf2Haps(genos, vaf)
        #    obj = GData(data={'rep'+str(num):haplotypes, 'maf':maf, 'annotation':sel, 'position':pos}, name='rep'+str(num))
        #    obj.compress()
        #    obj.sink('rep'+str(num))
        #    del obj
        #    os.chdir(cwd)
        #    #dictGenos[str(num)] = np.array(genos, dtype=np.uint8)
        endTime.append(time.clock())
        if verbose == -1:
            continue
        else:
            print 'Finished simulating replicate #', num
            print 'Time spent for simulating current replicate = ', round((endTime[num]-startTime[num])/60, 1), 'minutes'
            print 'Total time spent = ', round((sum(endTime)-sum(startTime))/60, 1), 'minutes'
            print '----------------------------------------------------------------------'
        ## remove unused objects
        #del pop, genos, maf, sel, pos, vaf
    # save genotype of individuals of different replicates into outfile.gdat by numpy.uint8 format
    if variantPool:
        bz2Save(fileName, tempFolder)
        #saveGenos(dictGenos, fileName)
    return
    

#def saveGenos(dictGenos, fileName):
#    '''
#    save genotype of individuals of many replicates into *.gdat by numpy.unit8 format
#    {1:[[geno], ..., [geno]], ..., n:[[geno], ..., [geno]]}
#    '''
#    dictGenos['file'] = fileName
#    np.savez(**dictGenos)
#    os.rename(fileName+'.npz', fileName+'.gdat')
#    return
#    
#
#def loadGenos(fileName):
#    return np.load(fileName+'.gdat')


def convertGenosToListOf2Haps(genos, vaf):
    '''
    convert genotype to haplotypes and swap wild-tyes with variants (0<->1) if variant allele frequency > 0.5
    '''
    haps = []
    lenHap = len(genos[0])/2
    for geno in genos:
        hap1, hap2 = [], []
        for i in range(lenHap):
            hap1.append(geno[i*2]), hap2.append(geno[(i*2)+1])
        haps.append(hap1)
        haps.append(hap2)
    #
    hapsArray = np.array(haps)
    for idx, f in enumerate(vaf):
        if f > 0.5:
            for i,x in enumerate(hapsArray[:,idx]):
                if x == 0:
                    hapsArray[i, idx] = 1
                elif x == 1:
                    hapsArray[i, idx] = 0
                else:
                    continue
    return hapsArray


def bz2Save(fileName, tempFolder):
    '''
    zip 'rep#' files in tempFolder to fileName and delete tempFolder
    '''
    repNames = glob.glob(os.path.join(tempFolder, '*'))
    tar = tarfile.open(fileName+'.gdat', 'w:bz2')
    cwd = os.getcwd()
    os.chdir(tempFolder)
    for name in repNames:
        #tar.add(name)
        tar.add(os.path.basename(name))
    # remove tempFolder
    shutil.rmtree(tempFolder)
    os.chdir(cwd)
    return


if __name__ == '__main__':
    pars = simuOpt.Params(options, 'Simulation of samples with rare variants', __doc__)
    if not pars.getParam():
        sys.exit(1)
    # 
    pop = srvOutput(regRange=pars.regRange, fileName=pars.fileName, numReps=pars.numReps,
                  N=pars.N, G=pars.G, mutationModel=pars.mutationModel, mu=pars.mu,
                  selModel=pars.selModel, selDist=pars.selDist, selCoef=pars.selCoef, selRange=pars.selRange,
                    recRate=pars.recRate, steps=pars.steps, verbose=pars.verbose,
                    saveGenotype=pars.saveGenotype, saveStat=pars.saveStat,
                    revertFixedSites=pars.revertFixedSites, variantPool=pars.variantPool)
