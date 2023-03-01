#!/usr/bin/env python

from future import standard_library
standard_library.install_aliases()
from builtins import range
from EMAN2 import *
import time
import os
import queue
from sys import argv,exit
from EMAN2jsondb import JSTask
import numpy as np
from scipy.optimize import minimize

def main():
	usage = """
	Alternative to e2spt_align.py, but uses 2D subtilt images instead of 3D particle volume for alignment. Also a modified scipy minimizer is used in place of EMAN2 aligners. 
	e2spt_align_subtlt.py sets/ptcls.lst reference.hdf --path spt_xx --iter x
"""

	parser = EMArgumentParser(usage=usage,version=EMANVERSION)
	
	parser.add_argument("--path",type=str,default=None,help="Path to a folder where results should be stored, following standard naming conventions (default = spt_XX)")
	parser.add_argument("--iter",type=int,help="Iteration number within path. Default = 0",default=0)
	parser.add_argument("--goldcontinue",action="store_true",help="Will use even/odd refs corresponding to specified reference to continue refining without phase randomizing again",default=False)
	parser.add_argument("--sym",type=str,default="c1",help="Symmetry of the input. Must be aligned in standard orientation to work properly.")
	parser.add_argument("--maxres",type=float,help="Maximum resolution to consider in alignment (in A, not 1/A)",default=0)
	parser.add_argument("--minres",type=float,help="Minimum resolution to consider in alignment (in A, not 1/A)",default=0)
	#parser.add_argument("--maxtilt",type=float,help="Excluding tilt images beyond the angle",default=-1)
	parser.add_argument("--parallel", type=str,help="Thread/mpi parallelism to use", default="thread:4")
	parser.add_argument("--fromscratch",action="store_true",help="Start from exhaustive coarse alignment. Otherwise will use alignment from the previous rounds and do local search only.",default=False)
	parser.add_argument("--use3d",action="store_true",help="use projection of 3d particles instead of 2d sub tilt series",default=False)
	parser.add_argument("--preprocess", metavar="processor_name:param1=value1:param2=value2", type=str, default=None, help="Preprocess each 2-D subtilt while loading (alignment only)")
	parser.add_argument("--debug",action="store_true",help="Debug mode. Will run a small number of particles directly without parallelism with lots of print out. ",default=False)
	parser.add_argument("--plst",type=str,default=None,help="list of 2d particle with alignment parameters. The program will reconstruct before alignment so it can be slower.")
	
	parser.add_argument("--maxshift", type=int, help="Maximum shift from the center of the box or the previous alignment. default box size//6",default=-1)
	parser.add_argument("--maxang", type=int, help="Maximum angle difference from starting point. Ignored when --fromscratch is on.",default=30)
	parser.add_argument("--curve",action="store_true",help="Mode for filament structure refinement. Still under testing. Ignored when --fromscratch is on.",default=False)
	parser.add_argument("--skipali",action="store_true",help="Skip alignment and only calculate the score. Incompatible with --fromscratch, but --breaksym will still be considered.",default=False)
	parser.add_argument("--breaksym",type=str,default=None,help="Specify symmetry to break. Ignored when --fromscratch is on.")
	
	parser.add_argument("--verbose", "-v", dest="verbose", action="store", metavar="n", type=int, default=0, help="verbose level [0-9], higher number means higher level of verboseness")
	parser.add_argument("--ppid", type=int, help="Set the PID of the parent process, used for cross platform PPID",default=-1)

	(options, args) = parser.parse_args()
	logid=E2init(sys.argv, options.ppid)

	options.ref=args[1]
	#### we assume the particle_info files are in the right place so the bookkeeping is easier
	##   they should be created by e2spt_refine_new or e2spt_refine_multi_new
	options.info3dname="{}/particle_info_3d.lst".format(options.path)
	options.info2dname="{}/particle_info_2d.lst".format(options.path)
	if options.preprocess!=None: options.preprocess = parsemodopt(options.preprocess)
		
	#### this should generate a list of dictionaries (one dictionary per 3d particle)
	tasks=load_lst_params(args[0])
	
	#### keep a copy of the particle index here, so we can make sure the output list has the same order as the input list
	for i,t in enumerate(tasks):
		t["ii"]=i

	from EMAN2PAR import EMTaskCustomer
	etc=EMTaskCustomer(options.parallel, module="e2spt_align_subtlt.SptAlignTask")
	num_cpus = etc.cpu_est()
	options.nowtime=time.time()
	
	#### only run a few particles for debugging
	if options.debug:
		tasks=tasks[:num_cpus*4]
		
	print("{} jobs on {} CPUs".format(len(tasks), num_cpus))
	njob=num_cpus
	
	#### send out jobs
	tids=[]
	for i in range(njob):
		t=tasks[i::njob]
		task=SptAlignTask(t, options)
		if options.debug:
			ret=task.execute(print)
			return 
		tid=etc.send_task(task)
		tids.append(tid)

	while 1:
		st_vals = etc.check_task(tids)
		if -100 in st_vals:
			print("Error occurs in parallelism. Exit")
			return
		E2progress(logid, np.mean(st_vals)/100.)
		
		if np.min(st_vals) == 100: break
		time.sleep(5)
	
	#### each job returns a 3-object tuple: 
	##     (index of 3d particle, 3d particle align info dict, list of dict for 2d particles align info)
	output3d=[None]*len(tasks)
	output2d=[None]*len(tasks)
	for i in tids:
		rets=etc.get_results(i)[1]
		for r in rets:
			output3d[r[0]]=r[1]
			output2d[r[0]]=r[2]
		
	del etc
	
	#### merge the 2d particle alignment info into one list
	output2d=sum(output2d, [])
	
	#### just dump the list of dictionaries to list files
	fm3d=f"{options.path}/aliptcls3d_{options.iter:02d}.lst"
	save_lst_params(output3d, fm3d)
	
	fm2d=f"{options.path}/aliptcls2d_{options.iter:02d}.lst"
	save_lst_params(output2d, fm2d)
	
	E2end(logid)

class SptAlignTask(JSTask):
	
	def __init__(self, data, options):
		
		JSTask.__init__(self,"SptAlign",data,{},"")
		self.options=options
	
	
	def execute(self, callback):
		
		#### this is loss function for scipy minimizer. 
		##   need to define this inside the function to use some local variables
		##     (options, initxf, mxsft, refsmall, pjxfsmall, imgsmallpjs, minp, maxp)
		def testxf(x, return_2d=False):
			if isinstance(x, Transform):
				txf=x
			else:
				txf=Transform({"type":"eman", "tx":x[0], "ty":x[1], "tz":x[2],"alt":x[3], "az":x[4], "phi":x[5]})
			
			if return_2d==False:
				if initxf:
					t=Transform(initxf.inverse())
					t.set_trans(t.get_trans()*ss/ny)
					t=t*txf
					sft=np.linalg.norm(t.get_trans())
					a=t.get_rotation("spin")["omega"]
					if sft>mxsft or a>options.maxang: return 1
					
				else:
					sft=np.linalg.norm(txf.get_trans())
					if sft>mxsft: return 1
		
			xfs=[p*txf for p in pjxfsmall]
			pjs=[refsmall.project('gauss_fft',{"transform":x, "returnfft":1}) for x in xfs]

			fscs=np.array([m.calc_fourier_shell_correlation(p) for m,p in zip(imgsmallpjs, pjs)])
			fscs=fscs.reshape((len(fscs), 3, -1))[:,1,:]
			scr=-np.mean(fscs[:, minp:maxp], axis=1)
			
			if return_2d:
				return scr
			else:
				return np.mean(scr)
		
		callback(0)
		rets=[]
		options=self.options
		info3d=load_lst_params(options.info3dname)
		
		#### do the even/odd split if required
		reffile=options.ref
		if options.goldcontinue:
			refnames=[reffile[:-4]+f"_{eo}.hdf" for eo in ["even", "odd"]]
		else:
			refnames=[reffile, reffile]
		
		#### maybe better to have some extra padding but haven't seen much benefit in tests
		ref=EMData(refnames[0],0,True)
		by=ref["ny"]
		ny=by#good_size(by*1.2)
		apix=ref["apix_x"]
		
		#### FT the references for faster projection later
		refs=[]
		for r in refnames:
			ref=EMData(r,0)
			ref=ref.get_clip(Region((by-ny)/2, (by-ny)/2,(by-ny)/2, ny, ny,ny))
			refft=ref.do_fft()
			refft.process_inplace("xform.phaseorigin.tocenter")
			refft.process_inplace("xform.fourierorigin.tocenter")
			refs.append(refft)
		
		#### keep the orientations to test for initial coarse alignment
		##   this is suprising memory consuming, but still tolarable. generating the orientations is a bit slow since we are doing this in python
		if options.fromscratch:
			astep=7.5-1e-5
			sym=Symmetries.get(options.sym)
			
			#### saff seems slightly better in some tests (in the coverage of the edge of asymmetrical units) but it is not entirely clear. it does not matter much in most cases
			xfcrs=sym.gen_orientations("saff",{"delta":astep,"phitoo":astep,"inc_mirror":1})
		
		#### resolution range
		if options.minres>0:
			minp=ceil(ny*apix/options.minres)
		else:
			minp=2
			
		if options.maxres>0:
			maxp=ceil(ny*apix/options.maxres)
			maxy=good_size(maxp*3)
			maxy=int(min(maxy, ny))
		else:
			maxy=ny
			maxp=int(ny*.4)
			
		#### fourier box sizes for iterative alignment
		##   same as the tree aligner, 24, 48, 64, 128, ...
		ssrg=2**np.arange(4,12, dtype=int)
		ssrg[:2]=ssrg[:2]*3/2
		ssrg=np.append(ssrg[ssrg<maxy], maxy)
		ssrg=ssrg.tolist()
		
		#### in case the resolution is very low or box size is versy small
		##   still run one iteration of local search
		if options.fromscratch and len(ssrg)==1: ssrg.append(maxy)
		
		#if options.preprocess!=None:
			#print(f"Applying {options.preprocess} to subtilts")
		data=self.data[0]
		if options.debug: print("max res: {:.2f}, max box size {}".format(options.maxres, maxy))
		for di,data in enumerate(self.data):
			
			#### prepare inputs
			##   keep original even/odd split if exist...
			if "orig_idx" in data:
				dii=data["orig_idx"]
			else:
				dii=data["ii"]
				
			ref=refs[dii%2]
			info=info3d[dii]
			
			#### read and FT the 3D particle
			##   this is actually only used when fromscratch is on. maybe should skip it otherwise, but need some testing first
			img=EMData(data["src"], data["idx"])
			img.process_inplace("filter.highpass.gauss",{"cutoff_pixels":4})
			img.process_inplace("normalize.edgemean")
			img.process_inplace("mask.soft",{"outer_radius":-1})
			img=img.get_clip(Region((by-ny)/2, (by-ny)/2,(by-ny)/2, ny, ny,ny))
			
			img=img.do_fft()
			img.process_inplace("xform.phaseorigin.tocenter")
			img.process_inplace("xform.fourierorigin.tocenter")
			
			#imgsrc=img["class_ptcl_src"]
			#imgidx=img["class_ptcl_idxs"]
			#imgcoord=img["ptcl_source_coord"]
			
			#### read projection transforms for the 2d particles
			info2d=load_lst_params(options.info2dname, info["idx2d"])
			pjxfs=[d["xform.projection"] for d in info2d]
			imgsrc=info2d[0]["src"]
			imgidx=[i["idx"] for i in info2d]
			tiltids=[d["tilt_id"] for d in info2d]
			
			#### if an existing aliptcls2d list is provided, replace the projection transforms
			if options.plst:
				pms=load_lst_params(options.plst, info["idx2d"])
				dxf=[d["dxf"] for d in pms]
				pjxfs=[d*p for d,p in zip(dxf, pjxfs)]
			
			#### prepare 2d subtilt particles
			imgpjs=[]
			if options.use3d:
				#### make 2d particle projections from 3d particles
				for i, pxf in enumerate(pjxfs): 
					m=img.project('gauss_fft',{"transform":pxf, "returnfft":1})
					m.process_inplace("xform.fourierorigin.tocenter")
					imgpjs.append(m)
			else:
				for i, pxf in enumerate(pjxfs): 
					#print(imgsrc, imgidx[i])
					m=EMData(imgsrc, imgidx[i])
					if options.preprocess!=None:
						m.process_inplace(options.preprocess[0],options.preprocess[1])

					m.clip_inplace(Region((m["nx"]-ny)//2, (m["ny"]-ny)//2, ny, ny))
					m=m.do_fft()
					m.process_inplace("xform.phaseorigin.tocenter")
					m.process_inplace("xform.fourierorigin.tocenter")
					
					imgpjs.append(m)
					
			
			initxf=None
			localsearch=False
			#### start from coarse alignment 
			if options.fromscratch:
				curxfs=[]
				npos=32
				ifirst=0
			
			#### read curve direction from header
			##   this is really not too different than --breaksym d18. maybe get rid of this entirely after some testing...
			elif options.curve:
				if img.has_attr("xform.align3d"):
					xf=img["xform.align3d"]#.inverse()
				else:
					xf=data["xform.align3d"]#.inverse()
				npos=32
				xfs=[Transform().get_params("eman") for i in range(npos)]
				for i,x in enumerate(xfs):
					x["phi"]+=(i*360*2/npos)%360
					x["alt"]+=(i>npos/2)*180
					
				curxfs=[Transform(x)*xf for x in xfs]
				curxfs=[x.inverse() for x in curxfs]
				xfcrs=[Transform(x) for x in curxfs]
				ifirst=1
				npos=32
				
			### do local search around the previous solution
			else:
				
				if "xform.align3d" in data:
					xf=data["xform.align3d"].inverse()
				else:
					e=EMData(data["src"],data["idx"], True)
					xf=e["xform.align3d"].inverse()
					
				#xf=data["xform.align3d"].inverse()
				if options.breaksym==None:
					curxfs=[xf]
				else:
					nsym=xf.get_nsym(options.breaksym)
					curxfs=[xf.get_sym(options.breaksym, i) for i in range(nsym)]
				npos=len(curxfs)
				ifirst=max(1,len(ssrg)-1)
				localsearch=True
			
			ilast=len(ssrg)

			#### 3d alignment loop. increase fourier box size and reduce solutions every iteration
			if options.debug: print("Align particle ( {}, {} )".format(data["src"], data["idx"]))
			for si in range(ifirst, ilast):
				ss=ssrg[si]
				if ss>=maxy: 
					ss=maxy
			
				if options.maxshift<0:
					mxsft=ss//6
				else:
					mxsft=int(options.maxshift*ss/ny)
					
				astep=89.999/floor((np.pi/(3*np.arctan(2./ss)))) ### copied from spt tree aligner
				newxfs=[]
				score=[]
				if options.debug: 
					print("size {}, solution {}, astep {:.1f}, maxres {:.1f}, xmult {:.1f}".format(
						ss, npos, astep,apix*2*ny/ss, ny/ss))

				refsmall=ref.get_clip(Region(0,(ny-ss)//2, (ny-ss)//2, ss+2, ss, ss))	
				
				if si==0:# and options.fromscratch:
					
					#### coarse alignment using 3d particles
					##   3d particles are needed for fast translation alignment by ccf
					##   at very low resolution the Fourier space has high coverage so it is probably fine...
					##   need to shift origin to rotate 3d volume
					imgsmall=img.get_clip(Region(0,(ny-ss)//2, (ny-ss)//2, ss+2, ss, ss))
					refsmall.process_inplace("xform.fourierorigin.tocenter")
					imgsmall.process_inplace("xform.fourierorigin.tocenter")
					
					for xf0 in xfcrs:
						xf=Transform(xf0)
						xf.set_trans(xf.get_trans()*ss/ny)
						refrot=refsmall.process("xform", {"transform":xf})
						ccf=imgsmall.calc_ccf(refrot)
						pos=ccf.calc_max_location_wrap(mxsft, mxsft, mxsft)
						
						refrot=refrot.process("xform", {"tx":pos[0], "ty":pos[1], "tz":pos[2]})
						scr=imgsmall.cmp("ccc.tomo.thresh", refrot)
						score.append(scr)
						xf.translate(pos)
						
						newxfs.append(xf)
						if options.debug: 
							sys.stdout.write("\r {}/{}    {:.3f}  ".format(len(newxfs), len(xfcrs), score[-1]).ljust(40))
							sys.stdout.flush()
					
				else:
					#### local orientation search
					## make small projections
					imgsmallpjs=[]
					for pj in imgpjs:
						m=pj.get_clip(Region(0,(ny-ss)//2, ss+2, ss))
						m.process_inplace("xform.fourierorigin.tocenter")
						imgsmallpjs.append(m)
					
					pjxfsmall=[Transform(p) for p in pjxfs]
					for p in pjxfsmall:
						p.set_trans(p.get_trans()*ss/ny)
						
					for xf in curxfs:
						x=xf.get_params("eman")
						x0=[x["tx"]*ss/ny, x["ty"]*ss/ny, x["tz"]*ss/ny, x["alt"], x["az"], x["phi"]]
						if localsearch:
							initxf=Transform(xf)
						
						#### still test the current orientation and return a score even if the alignmetn is skipped
						if options.skipali:
							x=x0
							s=testxf(x)
							#s=-np.random.rand()
							
						else:
							#### some other minimizers maybe better but Simplex is easy and fast...
							simplex=np.vstack([[0,0,0,0,0,0], np.eye(6)])
							simplex[4:]*=astep
							
							res=minimize(testxf, x0,  method='Nelder-Mead', options={'ftol': 1e-3, 'disp': False, "maxiter":50,"initial_simplex":simplex+x0})
							
							x=res.x
							s=float(res.fun)
						
						txf=Transform({"type":"eman", "tx":x[0], "ty":x[1], "tz":x[2],"alt":x[3], "az":x[4], "phi":x[5]})
						newxfs.append(txf)
						score.append(s)
						
						if options.debug: 
							scr0 = testxf(x0)
							sys.stdout.write("\r {}/{}    {:.3f}    {:.3f}     ".format(len(newxfs), len(curxfs), scr0, score[-1]))
							sys.stdout.flush()
				
				if options.debug: print()
					
				#### now look for a number of best orientations that are not too close to each other
				curxfs=newxfs
				newxfs=[]
				newscore=[]
				idx=np.argsort(score)
				for i in idx:
					dt=[(x.inverse()*curxfs[i]).get_params("spin")["omega"] for x in newxfs]
					if len(dt)==0 or np.min(dt)>astep*4:
						newxfs.append(curxfs[i])
						#### need to get the score per each 2d particle
						##   although this is probably only needed at the last iteration
						##   but it is safer to keep it here since we break out the loop in a number of places...
						if si==0: newscore.append(score[i])
						else: newscore.append(testxf(curxfs[i], True))
					if len(newxfs)>=npos:
						break
				
				for xi,xf in enumerate(newxfs):
					#### limit translation between -ny//2 and ny//2
					x=np.array(xf.get_trans()*ny/ss)
					x=x%ny
					x=x-ny*(x>ny//2)
					xf.set_trans(x.tolist())
						
					if options.debug: 
						x=xf.get_params("eman")
						x1=[np.mean(newscore[xi]),x["tx"], x["ty"], x["tz"], x["alt"], x["az"], x["phi"]]
						x1=["{:.4f}".format(a) for a in x1]
						print(' {} : {} - ( {} )'.format(idx[xi], x1[0], ', '.join(x1[1:])))
					
				npos=max(1, npos//2)
				curxfs=newxfs
				if ss>=maxy and si>0:
					break
				
			##############
			
			xfout=Transform(curxfs[0])
			score=newscore[0]
			#### to prevent some situation when we don't have score for each 2d particle
			##   this really shouldn't happen but just to be safe...
			try: s=score[0]
			except: score=[score for i in range(len(imgidx))]
			
			#### projection of the 2d particles with respect to the references
			imgxfs=[p*xfout for p in pjxfs]
			
			#### alignment info for 3d particles.
			c3d={	"src":data["src"], "idx":data["idx"], 
				"xform.align3d":xfout.inverse(), "score":np.mean(score)}
			
			if "orig_idx" in data:
				c3d["orig_idx"]=data["orig_idx"]
				clsid=data["orig_idx"]%2
			else:
				clsid=data["ii"]%2
			
			#### alignment info for 2d particles.
			c2d=[]
			for i in range(len(imgidx)):
				c={	"src":imgsrc, "idx":imgidx[i],
					"xform.projection": imgxfs[i], "score":score[i],
					"ptcl3d_id":data["ii"], "class": clsid, "tilt_id":tiltids[i]}
				
				c2d.append(c)
				
			rets.append((data["ii"], c3d, c2d))
			
			if options.debug:
				x=xfout.get_params("eman")
				x0=[x["tx"], x["ty"], x["tz"], x["alt"], x["az"], x["phi"]]
				#s0 = testxf(x0)
				x0=["{:.4f}".format(a) for a in x0]
				print('{} : {:.4f} - ( {} )'.format(data["ii"], np.mean(score), ', '.join(x0[1:])))
				
				print('#############')
				
			else:
				callback(len(rets)*100//len(self.data))

		
		return rets
		

if __name__ == "__main__":
	main()

