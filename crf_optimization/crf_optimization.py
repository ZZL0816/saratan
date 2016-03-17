import nlopt
import numpy as np

import logging
import config

from caffehelper import nifti_helper as nh
from denseinference import CRFProcessor

from multiprocessing import Pool

import scipy as sp


import medpy.metric

#global list for volumes
volumes = []


def downscale_img_label(imgvol,label_vol):
	"""
	Downscales an image volume and an label volume. Normalizes the hounsfield units of the image volume
	:param imgvol:
	:param label_vol:
	:return:
	"""
	imgvol_downscaled = np.zeros((config.slice_shape[0],config.slice_shape[1],imgvol.shape[2]))
	label_vol_downscaled = np.zeros((config.slice_shape[0],config.slice_shape[1],imgvol.shape[2]))

	for i in range(imgvol.shape[2]):
		#Get the current slice, normalize and downscale
		slice = np.copy(imgvol[:,:,i])

		slice[slice>1200] = 0

		slice = nh.hounsfield_to_float_dyn(slice)

		slice = sp.misc.imresize(slice,config.slice_shape)/255.

		#slice = histeq_processor(slice)

		imgvol_downscaled[:,:,i] = slice

		#downscale the label slice for the crf
		label_vol_downscaled[:,:,i] = sp.misc.imresize(label_vol[:,:,i],config.slice_shape,interp='nearest')
	return [imgvol_downscaled,label_vol_downscaled]

def crf_worker(img,label,prob,crfsettings):
	"""
	Worker function for Parallel CRF Processing of multiple Volumes
	:param img:
	:param label:
	:param prob:
	:param crfsettings:
	:return: lesion dice
	"""
	pro = CRFProcessor.CRF3DProcessor(**crfsettings)
	result = pro.set_data_and_run(img, probvol)
	lesion_dice = medpy.dc(result==2,label==2)


	#not sure if that's necessary
	del pro

	return lesion_dice


def run_crf(pos_x_std = 1.5
	, pos_y_std =  1.5
	, pos_z_std = 1.5
	, bilateral_x_std = 9.0
	, bilateral_y_std = 9.0
	, bilateral_z_std = 9.0
	, bilateral_intensity_std = 20.0
	, pos_w = 3.0
	, bilateral_w = 10.0):
	"""

	:param pos_x_std:
	:param bilateral_x_std:
	:param bilateral_intensity_std:
	:param pos_w:
	:param bilateral_w:
	:return:
	"""

	logging.info("=======================")
	logging.info("Running CRF with the following parameters:")
	logging.info("pos x std: " + str(pos_x_std))
	logging.info("pos y std: " + str(pos_y_std))
	logging.info("pos z std: " + str(pos_z_std))
	logging.info("pos w: " + str(pos_w))
	logging.info("bilateral x std: " + str(bilateral_x_std))
	logging.info("bilateral y std: " + str(bilateral_y_std))
	logging.info("bilateral z std: " + str(bilateral_z_std))
	logging.info("bilateral intensity std: " + str(bilateral_intensity_std))
	logging.info("bilateral w: " + str(bilateral_w))

	#Here's something to come
	crfsettings = dict(max_iterations=config.max_iterations,
                                    pos_x_std=pos_x_std,
                                    pos_y_std=pos_y_std,
                                    pos_z_std=pos_z_std,
                                    pos_w=pos_w,
                                    bilateral_x_std=bilateral_x_std,
                                    bilateral_y_std=bilateral_y_std,
                                    bilateral_z_std=bilateral_z_std,
                                    bilateral_intensity_std=bilateral_intensity_std,
                                    bilateral_w=bilateral_w,

                                    dynamic_z=config.dynamic_z,
                                    ignore_memory=config.ignore_memory)


	#list of dice scores
	dices = []
	#list of pipes
	processes = []

	pool = Pool(processes=config.N_PROC)

	#start processes
	for img, label, prob in volumes:
		processes.append(pool.apply_async(crf_worker,(img,label,prob,crfsettings)))

	#get results
	for p in processes:
		dices.append(p.get())

	dice_average = np.average(dices)

	logging.info("-----------------------")
	logging.info("Average lesion dice result: " + str(dice_average))
	logging.info("=======================")

	pool.close()

	return dice_average






if __name__ == '__main__':
	logging.basicConfig(filename=config.logfile, level=config.log_level, format='%(levelname)s:%(message)s', datefmt='%d-%m-%Y %I:%M:%S %p')

	#also print logs to stdout (ok, stderr technically...)
	logging.getLogger().addHandler(logging.StreamHandler())

	logging.info("Preparing volumes")
	for volume in config.dataset:
		imgvol = nh.load_nifti(volume[1]).get_data()
		labelvol = nh.load_nifti(volume[2]).get_data()
		probvol = np.load(volume[4])

		#rotate volumes so that the networks sees them in the same orientation like during training
		imgvol = np.rot90(imgvol)
		labelvol = np.rot90(labelvol)

		imgvol_downscaled, labelvol_downscaled = downscale_img_label(imgvol,labelvol)

		volumes.append([imgvol_downscaled,labelvol_downscaled,probvol])

	logging.info("Setting up Optimiser")
	#create optimiser object. We have 9 free parameters.
	opt = nlopt.opt(nlopt.LN_BOBYQA, 9)

	#The optimizer is supposed to maximise the dice that is returned by run_crf
	opt.set_max_objective(run_crf)

	opt.set_maxtime(config.MAX_N_IT)

	#Runs optimization
	logging.info("Running Optimisation")
	paramsopt = opt.optimize(**config.params_initial)

	logging.info("Done")