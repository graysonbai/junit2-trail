#!/usr/bin/python

import sys
import ConfigParser
import subprocess
import re
import ast
from testrail import *
from argparse import ArgumentParser

class OurDecorators :

    @staticmethod
    def handleAPIError() :
        def decorator( func ) :
            def wrapper( self, *args ) :
                try :
                    return func( self, *args )

                except APIError as e :
                    print( 'Error: %s' % e )
                    return None

            return wrapper
        return decorator


class JqAdapter :

    @staticmethod
    def _command( cmd ):
        try :
            return [ line for line in subprocess.check_output( cmd, shell = True ).decode( "utf-8" ).split( "\n" ) if line != "" ]

        except :
            return []

    @staticmethod
    def testedDevices( report ):
        return [ sid for sid in JqAdapter._command( "jq '.results | keys | .[]' {}".format( report ) ) if sid != "" ]

    @staticmethod
    def caseIdToFunctioName( report ):
        _res = {}
        _cmd = "jq '.results[].testResults[][1].log[].mMessage' {} | grep 'TestRailId: ' | sort | uniq"

        for line in JqAdapter._command( _cmd.format( report ) ):

            m = re.search( '<([\w]+)> TestRailId: C([\d]+)', line, re.IGNORECASE )
            if m is None:
                continue

            _function_name = m.group( 1 )
            _case_id       = m.group( 2 )

            if _case_id in _res.keys():
                _res[ _case_id ] += ( _function_name, )

            else:
                _res[ _case_id ] = ( _function_name, )

        return _res

    @staticmethod
    def removeMessageFromReport( report ):
        _report = "{}.nolog".format( report )
        JqAdapter._command( "jq 'del(.results[].testResults[][].log)' {} > {}".format( report, _report ) )
        return _report

    @staticmethod
    def functionNameToAutomationResult( report ):
        _res = {}
        _cmd = 'jq -jr \'.results[ %s ].testResults[] | reduce .[] as $item ( {}; . + $item) | .methodName, ",", .status, "\n"\' %s'

        for sid in JqAdapter.testedDevices( report ):

            for line in JqAdapter._command( _cmd % ( sid, report )  ):

                _tmp = line.split( "," )
                _func_name   = _tmp[ 0 ]
                _auto_result = _tmp[ 1 ]

                if _func_name in _res.keys():
                    _res[ _func_name ] += ( _auto_result, )
                else:
                    _res[ _func_name ] = ( _auto_result, )

        return _res


class TestRailAdapter:
    _client = None
    STATUS_IDS = { 'PASSED': 1,
                   'FIXED' : 1,
                   'RETEST': 4,
                   'FAILED': 5,
                   'REGRESSION': 5 }

    def __init__( self ):

        if self._client is None:
            parser = ConfigParser.ConfigParser()
            parser.read('testrail.cfg')
            self._client = APIClient( 'https://kkbox.testrail.net/' )
            self._client.user = parser.get( 'Test Rail', 'email' )
            self._client.password = parser.get( 'Test Rail', 'password' )

    @OurDecorators.handleAPIError()
    def runIds( self, plan_id = None ):
        if plan_id is None:
            raise Exception( "plan_id is not given" )

        run_ids = []
        response = self._client.send_get( 'get_plan/' + str( plan_id ) )
        test_suites = response[ 'entries' ]
        for suite in range( len( test_suites ) ) :
            test_runs = test_suites[ suite ][ 'runs' ]
            for run in range( len( test_runs ) ) :
                run_ids.append( test_suites[ suite ][ 'runs' ][ run ][ 'id' ] )

        return run_ids

    @OurDecorators.handleAPIError()
    def caseIds( self, run_id = None ) :
        if run_id is None:
            raise Exception( "run_id is not given" )

        return [ case[ 'case_id' ] for case in self._client.send_get( 'get_tests/' + str( run_id ) ) ]

    def dumpSpoonResultToTestPlan( self, spoonReports = [], testRunId = None, testMode = None) :
        if testRunId is None:
            raise Exception( "plan_id is not given" )

        # parsing result from 'result.json'
        for report in spoonReports:
            _caseIdToFunctionNames = JqAdapter.caseIdToFunctioName( report )
            _functionNameToAutomationResults = JqAdapter.functionNameToAutomationResult(
                                                    JqAdapter.removeMessageFromReport( report ) )

            for _caseId, _functionNames in JqAdapter.caseIdToFunctioName( report ).iteritems():
                if len( _functionNames ) > 1:
                    raise Exception( "one TestRailCaseId (%s) is mapped to multiple automation test cases (%s)" % (
                                        _caseId,
                                        ", ".join( _functionNames ) ) )

                _overallResult = None
                _automationResults = _functionNameToAutomationResults[ _functionNames[ 0 ] ]

                # tolerant mode
                if testMode == "0":
                    _overallResult = self.STATUS_IDS["PASSED"] if "PASS" in _automationResults else self.STATUS_IDS[
                        "FAILED"]

                # aggressive mode
                elif testMode == "1":
                    _overallResult = self.STATUS_IDS["FAILED"] if "FAIL" in _automationResults else self.STATUS_IDS[
                        "PASSED"]

                # retest mode
                elif testMode == "2":
                    _overallResult = self.STATUS_IDS["RETEST"]

                _uri  = "add_result_for_case/{}/{}".format( testRunId, _caseId )
                _data = { 'status_id': _overallResult,
                          'comment'  : 'test' }

                print( "Update Case 'C%s' to test run [%s] as %s" % ( _caseId, testRunId, _overallResult ) )
                self._client.send_post( _uri, _data )


if __name__ == "__main__":
    parser = ArgumentParser(description="Dump SpoonReport to TestRail")
    parser.add_argument("runId",
                        help= "testRunId on testrail")

    parser.add_argument("mode",
                        help= "test Mode")

    parser.add_argument("-p", "--post-result",
                        help= "Post result to TestRail",
                        dest= "post",
                        action= "store_true")

    args = parser.parse_args()

    TestRailAdapter().dumpSpoonResultToTestPlan(
        spoonReports=["./report/smoke-report/result.json",
                      "./report/rat-critical-report/result.json"],
        testRunId = args.runId,
        testMode = args.mode
    )
