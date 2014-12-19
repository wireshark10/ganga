################################################################################
# Ganga - a computational task management tool for easy access to Grid resources
# http://cern.ch/ganga
#
# $Id: Coordinator.py,v 1.1.4.2 2009-07-14 14:44:17 ebke Exp $
#
# Copyright (C) 2003-2007 The Ganga Project
#
# This file is part of Ganga. 
#
# Ganga is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Ganga is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
################################################################################

"""
 Internal services coordinator :
  takes care of conditional enabling/disabling of internal services (job monitoring loop, job registry, job
  repository/workspace) when credentials become invalid preventing normal functioning of these services.
  E.g: invalid grid proxy triggers the monitor-loop stop
"""
from Ganga.Utility.Config import getConfig
from Ganga.Utility.logging import getLogger

log = getLogger()

#the overall state of Ganga internal services
servicesEnabled = True

def isCredentialRequired (credObj):
    """
    The logic to decide if a given invalid credential
    should trigger the deactivation of Ganga internal services.  
    """

    from Ganga.Runtime import Workspace_runtime
    from Ganga.Runtime import Repository_runtime

    if credObj.__class__.__name__ == 'AfsToken':
        return Workspace_runtime.requiresAfsToken() or Repository_runtime.requiresAfsToken()

    if credObj.__class__.__name__ == 'GridProxy':
        if Repository_runtime.requiresGridProxy() or Workspace_runtime.requiresGridProxy():
           return True 
        from Ganga.GPI import jobs,typename
        for j in jobs:
            ji = j._impl
            if ji.status in ['submitted','running','completing'] and typename(ji.backend)=='LCG':
                return True
        return False
    
    log.warning("Unknown credential object : %s" % credObj)
    
def notifyInvalidCredential (credObj):
    """
    The Core is notified when one of the monitored credentials is invalid
    @see ICredential.create()
    """

    #ignore this notification if the internal services are already stopped 
    if not servicesEnabled:
        log.debug ("One of the monitored credential [%s] is invalid BUT the internal services are already disabled." % credObj._name)
        return

    if isCredentialRequired(credObj):
        log.debug ("One of the required credential for the internal services is invalid: [%s]."
                   "Disabling internal services ..." % credObj._name)
        _tl = credObj.timeleft()        
        if _tl == "-1":
            log.error('%s has been destroyed! Could not shutdown internal services.' % credObj._name)
            return        
        disableInternalServices()
        log.warning('%s is about to expire! '
                    'To protect against possible write errors all internal services has been disabled.'
                    'If you believe the problem has been solved type "reactivate()" to re-enable '
                    'interactions within this session.' %  credObj._name)
    else:
        log.debug ("One of the monitored credential [%s] is invalid BUT it is not required by the internal services" % credObj._name)


def _diskSpaceChecker():
    """
    the callback function used internally by Monitoring Component
    Reads and calls the checking function provided in the configuration. 
    If this checking function returns "False" the internal services are disabled making Ganga read-only:
    e.g:
    [PollThread]
    DiskSpaceChecker =  
        import commands
        diskusage = commands.getoutput('df -l -P %s/workspace' % config['Configuration']['gangadir'])
        used  = diskusage.splitlines()[1].split()[4] # get disk usage (in %)
        return int(used[:-1])<70
    """
    log.debug("Checking disk space")
    try:        
        config = getConfig('PollThread')
        
        if config['DiskSpaceChecker']:
            _checker = lambda : True
            try:
                #create the checker                
                from Ganga.Runtime import _prog
                import new
                ns={}
                code = "def check():"
                for line in config['DiskSpaceChecker'].splitlines():
                    code +="\t%s\n" % line
                exec code in ns
                _checker = new.function(ns["check"].func_code, _prog.local_ns, 'check' )
            except Exception,e:
                log.warning('Syntax errors in disk space checking code: %s. See [PollThread]DiskSpaceChecker' % e)
                return False

            #call the checker
            if _checker() is False:
                disableInternalServices()
                log.warning('You are running out of disk space! '
                    'To protect against possible write errors all internal services has been disabled.'
                    'If you believe the problem has been solved type "reactivate()" to re-enable '
                    'interactions within this session.')
    except Exception, msg:
        log.warning('Exception in free disk space checking code: %s. See [PollThread]DiskSpaceChecker' % msg)        
        return False
    return True


def disableInternalServices():
    """
    Deactivates all the internal services :
          * monitoring loop
          * registry/repository and workspace (or GPI entierly)
    Currently this method is called whenever:
          * one of the managed credentials (AFS token or Grid Proxy) is detected as beeing *invalid* by the monitoring component
          * the user is running out of space
    """

    from Ganga.Utility.logging import getLogger
    logger = getLogger()

    logger.info( "Ganga is now attempting to shut down all running processes accessing the repository in a clean manner" )
    logger.info( " ... Please be patient! " )

    global servicesEnabled
    log.debug("Disabling the internal services")

    #disable the mon loop
    logger.debug( "Shutting down the main monitoring loop" )
    from Ganga.Core.MonitoringComponent.Local_GangaMC_Service import _purge_actions_queue, stop_and_free_thread_pool
    _purge_actions_queue()
    stop_and_free_thread_pool()
    logger.debug( "Disabling the central Monitoring" )
    from Ganga.Core import monitoring_component
    monitoring_component.disableMonitoring()

    try:
       logger.debug( "Attempting to import GPI queues" )
       from Ganga.GPI import queues
       while queues.totalNumAllThreads() != 0:
          logger.debug( "Ensuring that all tasks are purged from the todo!" )
          queues._purge_all()
          import time
          time.sleep(2)
       queues._stop_all_threads()
    except:
       #TODO replace this with a nicer check to see if GangaDirac is loaded at all
       pass

    # For debugging what services are still alive after being requested to stop before we close the repository
    #from Ganga.Core.MonitoringComponent.Local_GangaMC_Service import getStackTrace
    #getStackTrace()
    #print queues_threadpoolMonitor._display(0)

    logger.debug( "Ganga is now about to shutdown the repository, any errors after this are likely due to badly behaved services" )

    logger.info( "Ganga is shutting down the repository, to regain access, type 'reactivate()' at your prompt" )

    #flush the registries
    log.debug( "Coordinator Shutting Down Repository_runtime" )
    from Ganga.Runtime import Repository_runtime
    Repository_runtime.shutdown()

    #this will disable any interactions with the registries (implicitly with the GPI)
    servicesEnabled = False    
    
def enableInternalServices():
    """
    activates the internal services previously disabled due to expired credentials
    """
    global servicesEnabled
    #make sure all required credentials are valid
    missing_cred = getMissingCredentials()
    if missing_cred:
        log.error("The following credentials are still required: %s."
                  "Make sure you renew them before reactivating this session" % ','.join(missing_cred))
        return
    #startup the registries
    from Ganga.Runtime import Repository_runtime
    Repository_runtime.bootstrap()

    log.debug("Enabling the internal services")
    # reenable the monitoring loop if *autostart* is set
    from Ganga.Core import monitoring_component
    from Ganga.Core.MonitoringComponent.Local_GangaMC_Service import config
    if config['autostart']:
        monitoring_component.enableMonitoring()

    servicesEnabled = True
    log.info('Internal services reactivated successfuly')

    try:
        from Ganga.GPI import queues
        queues._start_all_threads()
    except:
        pass

def checkInternalServices(errMsg='Internal services disabled. Job registry is read-only.'):
    """
    Check the state of internal services and return a ReadOnlyObjectError exception
    in case the state is disabled.    
    """
    
    global servicesEnabled
    from Ganga.GPIDev.Base import ReadOnlyObjectError
    
    if not servicesEnabled:
        raise ReadOnlyObjectError(errMsg)

def getMissingCredentials():
    """
    get a list of missing credentials
    i.e:  invalid credentials that are needed by the internal services to run
    """    
    from Ganga.GPIDev.Credentials import _allCredentials as availableCreds
    return [name for name in availableCreds \
            if not availableCreds[name].isValid() and \
            isCredentialRequired(availableCreds[name])]

def bootstrap():

    global servicesEnabled
    servicesEnabled = True
    
    #export to GPI 
    from Ganga.Runtime.GPIexport import exportToGPI
    exportToGPI('reactivate',enableInternalServices,'Functions') 
    exportToGPI('disableServices',disableInternalServices,'Functions')


#
#$Log: not supported by cvs2svn $
#Revision 1.1.4.1  2009/07/08 11:18:21  ebke
#Initial commit of all - mostly small - modifications due to the new GangaRepository.
#No interface visible to the user is changed
#
#Revision 1.1  2008/07/17 16:40:50  moscicki
#migration of 5.0.2 to HEAD
#
#the doc and release/tools have been taken from HEAD
#
#Revision 1.3.6.4  2008/03/11 15:22:42  moscicki
#merge from Ganga-5-0-restructure-config-branch
#
#Revision 1.3.6.3.2.1  2008/03/07 13:36:07  moscicki
#removal of [DefaultJobRepository] and [FileWorkspace]
#new options in [Configuration] user, gangadir, repositorytype, workspacetype
#
#Revision 1.3.6.3  2008/02/12 09:25:52  amuraru
#fixed repositories shutdown
#
#Revision 1.3.6.2  2008/02/05 12:33:23  amuraru
#fixed DiskSpaceChecker alignment
#
#Revision 1.3.6.1  2007/12/10 19:24:42  amuraru
#merged changes from Ganga 4.4.4
#
#Revision 1.7  2007/12/05 12:42:54  amuraru
#Ganga/Core/InternalServices/Coordinator.py
#
#Revision 1.6  2007/12/04 12:59:03  amuraru
#*** empty log message ***
#
#Revision 1.5  2007/11/26 14:04:30  amuraru
#allow indentation using \t tab char in DiskSpaceChecker
#
#Revision 1.4  2007/10/29 14:06:00  amuraru
# added free disk space checker to monitoring loop
#
#Revision 1.3  2007/07/27 18:02:34  amuraru
#updated to comply with the latest requirement for GPI free functions docstrings
#
#Revision 1.2  2007/07/27 14:31:56  moscicki
#credential and clean shutdown updates from Adrian (from Ganga-4-4-0-dev-branch)
#
#Revision 1.1.2.1  2007/07/27 13:04:00  amuraru
#*** empty log message ***
#
#
