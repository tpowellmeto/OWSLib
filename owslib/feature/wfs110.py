# -*- coding: ISO-8859-15 -*-
# =============================================================================
# Copyright (c) 2011 Tom Kralidis
#
# Authors : Tom Kralidis <tomkralidis@hotmail.com>
#
# Contact email: tomkralidis@hotmail.com
# =============================================================================

import cgi
from cStringIO import StringIO
from urllib import urlencode
from urllib2 import urlopen
import logging
from owslib.util import openURL, testXMLValue, nspath_eval
from owslib.etree import etree
from owslib.fgdc import Metadata
from owslib.iso import MD_Metadata
from owslib.ows import *
from owslib.filter import *

namespaces = {
    'gml': 'http://www.opengis.net/gml',
    'ogc': 'http://www.opengis.net/ogc',
    'ows': 'http://www.opengis.net/ows',
    'wfs': 'http://www.opengis.net/wfs'
}

class WebFeatureService_1_1_0(object):
    """Abstraction for OGC Web Feature Service (WFS).

    Implements IWebFeatureService.
    """
    def __new__(self,url, version, xml):
        """ overridden __new__ method 
        
        @type url: string
        @param url: url of WFS capabilities document
        @type xml: string
        @param xml: elementtree object
        @return: initialized WebFeatureService_1_1_0 object
        """
        obj=object.__new__(self)
        obj.__init__(url, version, xml)
        self.log = logging.getLogger()
        consoleh  = logging.StreamHandler()
        self.log.addHandler(consoleh)    
        return obj
    
    def __getitem__(self,name):
        ''' check contents dictionary to allow dict like access to service layers'''
        if name in self.__getattribute__('contents').keys():
            return self.__getattribute__('contents')[name]
        else:
            raise KeyError, "No content named %s" % name
    
    
    def __init__(self, url, version, xml=None):
        """Initialize."""
        self.url = url
        self.version = version
        self._capabilities = None
        self.owscommon = OwsCommon('1.0.0')
        reader = WFSCapabilitiesReader(self.version)
        if xml:
            self._capabilities = reader.readString(xml)
        else:
            self._capabilities = reader.read(self.url)
        self._buildMetadata()
    
    def _buildMetadata(self):
        '''set up capabilities metadata objects: '''

        # ServiceIdentification
        val = self._capabilities.find(util.nspath_eval('ows:ServiceIdentification', namespaces))
        self.identification=ServiceIdentification(val,self.owscommon.namespace)
        # ServiceProvider
        val = self._capabilities.find(util.nspath_eval('ows:ServiceProvider', namespaces))
        self.provider=ServiceProvider(val,self.owscommon.namespace)
        # ServiceOperations metadata
        self.operations=[]
        for elem in self._capabilities.findall(util.nspath_eval('ows:OperationsMetadata/ows:Operation', namespaces)):
            self.operations.append(OperationsMetadata(elem, self.owscommon.namespace))

        # FilterCapabilities
        val = self._capabilities.find(util.nspath_eval('ogc:Filter_Capabilities', namespaces))
        self.filters=FilterCapabilities(val)

        #serviceContents metadata: our assumption is that services use a top-level 
        #layer as a metadata organizer, nothing more. 
        
        self.contents={} 
        featuretypelist=self._capabilities.find(nspath_eval('wfs:FeatureTypeList', namespaces))
        features = self._capabilities.findall(nspath_eval('wfs:FeatureTypeList/FeatureType', namespaces))
        for feature in features:
            cm=ContentMetadata(feature, featuretypelist)
            self.contents[cm.id]=cm       
        
        #exceptions
        self.exceptions = [f.text for f \
                in self._capabilities.findall('Capability/Exception/Format')]
      
    def getcapabilities(self):
        """Request and return capabilities document from the WFS as a 
        file-like object.
        NOTE: this is effectively redundant now"""
        reader = WFSCapabilitiesReader(self.version)
        return urlopen(reader.capabilities_url(self.url))
    
    def items(self):
        '''supports dict-like items() access'''
        items=[]
        for item in self.contents:
            items.append((item,self.contents[item]))
        return items
    
    def getfeature(self, typename=None, filter=None, bbox=None, featureid=None,
                   featureversion=None, propertyname=['*'], maxfeatures=None,
                   srsname=None, method='{http://www.opengis.net/wfs}Get'):
        """Request and return feature data as a file-like object.
        
        Parameters
        ----------
        typename : list
            List of typenames (string)
        filter : string 
            XML-encoded OGC filter expression.
        bbox : tuple
            (left, bottom, right, top) in the feature type's coordinates.
        featureid : list
            List of unique feature ids (string)
        featureversion : string
            Default is most recent feature version.
        propertyname : list
            List of feature property names. '*' matches all.
        maxfeatures : int
            Maximum number of features to be returned.
        method : string
            Qualified name of the HTTP DCP method to use.
        srsname: string
            EPSG code to request the data in
            
        There are 3 different modes of use

        1) typename and bbox (simple spatial query)
        2) typename and filter (more expressive)
        3) featureid (direct access to known features)
        """
        base_url = self.getOperationByName('{http://www.opengis.net/wfs}GetFeature').methods[method]['url']
        request = {'service': 'WFS', 'version': self.version, 'request': 'GetFeature'}
        
        # check featureid
        if featureid:
            request['featureid'] = ','.join(featureid)
        elif bbox and typename:
            request['bbox'] = ','.join([repr(x) for x in bbox])
        elif filter and typename:
            request['filter'] = str(filter)
        
        if srsname:
            request['srsname'] = str(srsname)
            
        assert len(typename) > 0
        request['typename'] = ','.join(typename)
        
        request['propertyname'] = ','.join(propertyname)
        if featureversion: request['featureversion'] = str(featureversion)
        if maxfeatures: request['maxfeatures'] = str(maxfeatures)

        data = urlencode(request)
        u = openURL(base_url, data, method)
        
        
        # check for service exceptions, rewrap, and return
        # We're going to assume that anything with a content-length > 32k
        # is data. We'll check anything smaller.
        try:
            length = int(u.info()['Content-Length'])
            have_read = False
        except (KeyError, AttributeError):
            data = u.read()
            have_read = True
            length = len(data)
     
        if length < 32000:
            if not have_read:
                data = u.read()
            tree = etree.fromstring(data)
            if tree.tag == "{%s}ServiceExceptionReport" % OGC_NAMESPACE:
                se = tree.find(nspath_eval('ServiceException', OGC_NAMESPACE))
                raise ServiceException, str(se.text).strip()

            return StringIO(data)
        else:
            if have_read:
                return StringIO(data)
            return u

    def getOperationByName(self, name):
        """Return a named content item."""
        for item in self.operations:
            if item.name == name:
                return item
        raise KeyError, "No operation named %s" % name

class ContentMetadata:
    """Abstraction for WFS metadata.
    
    Implements IMetadata.
    """

    def __init__(self, elem):
        """."""
        self.id = testXMLValue(elem.find(nspath_eval('wfs:Name', namespaces)))
        self.title = testXMLValue(elem.find(nspath_eval('Title', namespaces)))
        self.abstract = testXMLValue(elem.find(nspath_eval('wfs:Abstract', namespaces)))
        self.keywords = [f.text for f in elem.findall(nspath_eval('ows:Keywords/ows:Keyword', namespaces))]

        # bbox
        self.boundingBoxWGS84 = None
        b = BoundingBox(elem.find(nspath_eval('ows:WGS84BoundingBox')))
        if b is not None:
            self.boundingBoxWGS84 = (
                    float(b.minx), float(b.miny),
                    float(b.maxx), float(b.maxy),
                    )
        # crs options
        self.crsOptions = [srs.text for srs in elem.findall(nspath_eval('wfs:OtherSRS', namespaces))]
        dsrs = testXMLValue(elem.find(nspath_eval('wfs:DefaultSRS', namespaces)))
        if dsrs is not None:  # first element is default srs
            self.crsOptions.insert(0, dsrs)

        # verbs
        self.verbOptions = [op.text for op in elem.findall(nspath_eval('wfs:Operations/wfs:Operation', namespaces))]

        # output formats
        self.verbOptions = [op.text for op in elem.findall(nspath_eval('wfs:OutputFormats/wfs:Format', namespaces))]

        # MetadataURLs
        self.metadataUrls = []
        for m in elem.findall(nspath('MetadataURL')):
            metadataUrl = {
                'type': testXMLValue(m.attrib['type'], attrib=True),
                'format': testXMLValue(m.find('Format')),
                'url': testXMLValue(m)
            }

            if metadataUrl['url'] is not None:  # download URL
                try:
                    content = urlopen(metadataUrl['url'])
                    doc = etree.parse(content)
                    if metadataUrl['type'] is not None:
                        if metadataUrl['type'] == 'FGDC':
                            metadataUrl['metadata'] = Metadata(doc)
                        if metadataUrl['type'] == 'TC211':
                            metadataUrl['metadata'] = MD_Metadata(doc)
                except Exception, err:
                    metadataUrl['metadata'] = None

            self.metadataUrls.append(metadataUrl)

        #others not used but needed for iContentMetadata harmonisation
        self.styles=None
        self.timepositions=None

class OperationMetadata:
    """Abstraction for WFS metadata.
    
    Implements IMetadata.
    """
    def __init__(self, elem):
        """."""
        self.name = elem.tag
        # formatOptions
        self.formatOptions = [f.tag for f in elem.findall(nspath('ResultFormat/*'))]
        methods = []
        for verb in elem.findall(nspath('DCPType/HTTP/*')):
            url = verb.attrib['onlineResource']
            methods.append((verb.tag, {'url': url}))
        self.methods = dict(methods)


class WFSCapabilitiesReader(object):
    """Read and parse capabilities document into a lxml.etree infoset
    """

    def __init__(self, version='1.0'):
        """Initialize"""
        self.version = version
        self._infoset = None

    def capabilities_url(self, service_url):
        """Return a capabilities url
        """
        qs = []
        if service_url.find('?') != -1:
            qs = cgi.parse_qsl(service_url.split('?')[1])

        params = [x[0] for x in qs]

        if 'service' not in params:
            qs.append(('service', 'WFS'))
        if 'request' not in params:
            qs.append(('request', 'GetCapabilities'))
        if 'version' not in params:
            qs.append(('version', self.version))

        urlqs = urlencode(tuple(qs))
        return service_url.split('?')[0] + '?' + urlqs

    def read(self, url):
        """Get and parse a WFS capabilities document, returning an
        instance of WFSCapabilitiesInfoset

        Parameters
        ----------
        url : string
            The URL to the WFS capabilities document.
        """
        request = self.capabilities_url(url)
        u = urlopen(request)
        return etree.fromstring(u.read())

    def readString(self, st):
        """Parse a WFS capabilities document, returning an
        instance of WFSCapabilitiesInfoset

        string should be an XML capabilities document
        """
        if not isinstance(st, str):
            raise ValueError("String must be of type string, not %s" % type(st))
        return etree.fromstring(st)
    