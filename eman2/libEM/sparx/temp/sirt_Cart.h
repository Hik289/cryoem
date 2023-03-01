/*
 * Author: Pawel A.Penczek, 09/09/2006 (Pawel.A.Penczek@uth.tmc.edu)
 * Please do not copy or modify this file without written consent of the author.
 * Copyright (c) 2000-2019 The University of Texas - Houston Medical School
 *
 * This software is issued under a joint BSD/GNU license. You may use the
 * source code in this file under either license. However, note that the
 * complete EMAN2 and SPARX software packages have some GPL dependencies,
 * so you are responsible for compliance with the licenses of these packages
 * if you opt to use BSD licensing. The warranty disclaimer below holds
 * in either instance.
 *
 * This complete copyright notice must be included in any revised version of the
 * source code. Additional authorship citations may be added, but existing
 * author citations must be preserved.
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
 *
 */
#ifndef SIRT_CART_H
#define SIRT_CART_H
#include "mpi.h"
#include "emdata.h"

using namespace EMAN;

#ifndef PI
#define PI 3.141592653589793
#endif

int recons3d_sirt_mpi_Cart(MPI_Comm comm_2d, MPI_Comm comm_row, MPI_Comm comm_col, EMData ** images, float * angleshift, EMData *& xvol, int nangloc, int radius = -1, float lam = 1.0e-4, int maxit = 100, std::string symmetry = "c1", float tol = 1.0e-3);

#endif // SIRT_CART_H
