"""Rebuild the separately licensed OpenMotor view from the bundled source.

Optional developer dependency: cadquery-ocp==8.0.1.0.0 plus numpy.
No dependency on a CAD kernel is added to the Apex runtime.
Geometry and generated metadata remain under the source asset license;
see data/reference/openmotor/README.md. Run from any directory.
"""
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TDocStd import TDocStd_Document
from OCP.TCollection import TCollection_ExtendedString,TCollection_AsciiString
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from OCP.collections import Sequence_TDF_Label,IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.RWGltf import RWGltf_CafWriter
from OCP.Message import Message_ProgressRange
import gzip,tempfile,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
TEMP=tempfile.TemporaryDirectory(prefix='apex-cad-build-')
source=pathlib.Path(TEMP.name)/'source.step'
source.write_bytes(gzip.decompress((ROOT/'data/reference/openmotor/source.step.gz').read_bytes()))
import hashlib
assert hashlib.sha256(source.read_bytes()).hexdigest()=='0f6737f1ddba820376e88298cf05725de36048f03c227714bf391e7cf21b07d3', 'Source revision changed'
from OCP.IFSelect import IFSelect_RetDone
r=STEPCAFControl_Reader();assert r.ReadFile(str(source))==IFSelect_RetDone
d=TDocStd_Document(TCollection_ExtendedString('MDTV-XCAF'));assert r.Transfer(d)
s=XCAFDoc_DocumentTool.ShapeTool_s(d.Main());seq=Sequence_TDF_Label();s.GetFreeShapes(seq)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID
solid_count=0
for i in range(1,seq.Length()+1):
 explorer=TopExp_Explorer(s.GetShape_s(seq.Value(i)),TopAbs_SOLID)
 while explorer.More():
  assert BRepCheck_Analyzer(explorer.Current()).IsValid(), 'Invalid solid in source'
  solid_count+=1;explorer.Next()
assert solid_count==616
for i in range(1,seq.Length()+1):BRepMesh_IncrementalMesh(s.GetShape_s(seq.Value(i)),.25,False,.35,True)
w=RWGltf_CafWriter(TCollection_AsciiString(str(pathlib.Path(TEMP.name)/'motor.glb')),True)
print('Export',w.Perform(d,IndexedDataMap_TCollection_AsciiString_TCollection_AsciiString(),Message_ProgressRange()))
import struct,json,pathlib
b=(pathlib.Path(TEMP.name)/'motor.glb').read_bytes();n=struct.unpack_from('<I',b,12)[0];j=json.loads(b[20:20+n])
(ROOT/'dashboard/static/models/openmotor.glb.gz').write_bytes(gzip.compress(b,mtime=0))
print('Bytes',len(b),'nodes',len(j['nodes']),'meshes',len(j['meshes']))


import json,numpy as np,itertools,hashlib,pathlib
parts=[];bounds=[]
def matrix(n):
 if 'matrix' in n:return np.array(n['matrix']).reshape((4,4),order='F')
 x,y,z,w=n.get('rotation',[0,0,0,1]);r=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
 m=np.eye(4);m[:3,:3]=r@np.diag(n.get('scale',[1,1,1]));m[:3,3]=n.get('translation',[0,0,0]);return m
def walk(i,parent,path):
 n=j['nodes'][i];m=parent@matrix(n);trail=path+[n.get('name',str(i))]
 if 'mesh' in n:
  points=[]
  for p in j['meshes'][n['mesh']]['primitives']:
   a=j['accessors'][p['attributes']['POSITION']]
   points.extend((m@[*xyz,1])[:3] for xyz in itertools.product(*zip(a['min'],a['max'])))
  pts=np.array(points);low,high=pts.min(axis=0),pts.max(axis=0);bounds.extend([low,high]);dims=np.round((high-low)*1000,2).tolist()
  parts.append(dict(id=f'node-{i}',node=i,name=trail[-1],group=trail[1] if len(trail)>2 else 'Assembly',hierarchy=trail[1:],dimensions_mm=dims,
   purpose='A named component from the source CAD assembly. Its functional explanation has not yet been independently reviewed.',
   connection='Source hierarchy: '+' / '.join(trail),model_note='CAD bounding size: '+' × '.join(str(v) for v in dims)+' mm. These are axis-aligned mesh bounds, not toleranced drawing dimensions.',source='cad'))
 for child in n.get('children',[]):walk(child,m,trail)
for i in j['scenes'][j.get('scene',0)]['nodes']:walk(i,np.eye(4),[])
b=np.array(bounds);dims=np.round((b.max(axis=0)-b.min(axis=0))*1000,2).tolist()
manifest=dict(id='openmotor-125',revision='1.0',geometry_revision='1',title='OpenMotor 125/25',subtitle='Source CAD · component study',fidelity='Source CAD · engineering review pending',asset='/api/study/model/openmotor-125/asset',dimensions_mm=dims,
 limitations='Converted from the published OpenMotor STEP assembly. CAD-kernel checks found 616 solid occurrences and no invalid solids; this is not a performance or manufacturing validation. The viewer exposes 135 named mesh occurrences, some containing multiple solids. A geometry-free Insulation node was omitted by conversion. Mesh bounds are approximate; display colors are not material properties. No simulation or independent engineering review.',
 validation=dict(purpose='Detailed source-geometry inspection',geometry='Published STEP converted to a tessellated view; original source included',dimensions='Source mm; mesh bounding sizes only; no tolerance validation',materials='Source appearance only; material properties not verified',physics='No electrical, magnetic, thermal or load simulation',review='616 solids passed kernel validity checks; independent engineering review pending'),
 sources=[dict(id='cad',title='OpenMotor hardware · pinned source',url='https://github.com/eMotres/OpenMotor-Hardware/tree/1e1e56d7cf64ea393793ca5c06189251f87b6e98',license='CERN-OHL-W-2.0')],parts=parts)
p=ROOT/'data/assemblies/openmotor-125.json';p.write_text(json.dumps(manifest,indent=2)+'\n');print('Parts',len(parts),'bounds mm',dims)

TEMP.cleanup()
