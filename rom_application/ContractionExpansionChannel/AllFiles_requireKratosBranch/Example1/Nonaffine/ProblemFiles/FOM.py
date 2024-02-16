import KratosMultiphysics
from KratosMultiphysics.FluidDynamicsApplication.fluid_dynamics_analysis import FluidDynamicsAnalysis

import KratosMultiphysics.RomApplication as romapp
import json

from KratosMultiphysics.RomApplication.empirical_cubature_method import EmpiricalCubatureMethod
from KratosMultiphysics.RomApplication.randomized_singular_value_decomposition import RandomizedSingularValueDecomposition

import numpy as np
from matplotlib import pyplot as plt

#importing overlapping strategies
from overlapping_strategies import time_clustering, narrowing_clustering, solution_manifold_clustering

#for checking if paths exits
import os

#importing PyGeM tools
from pygem import FFD, RBF

import pdb


class FOM_Class(FluidDynamicsAnalysis):

    def __init__(self, model, project_parameters):
        super().__init__(model, project_parameters)
        self.deformation_multiplier = 1 # original narrowing size
        time_step_size = self.project_parameters["solver_settings"]["fluid_solver_settings"]["time_stepping"]["time_step"].GetDouble()
        self.delta_deformation = time_step_size # this ensures to obtain the same deformation independently of the time step used
        self.control_point = 854 #a node around the middle of the geometry to capture the bufurcation
        self.maximum = 11
        self.minimum = 0
        ###  ###  ###
        self.node_up = 412      #nodes to obtain the narrowing width
        self.node_down = 673
        ###  ###  ###
        self.time_step_solution_container = []
        self.velocity_y_at_control_point = []
        self.narrowing_width = []
        self.deformation_multiplier_list = []
        self.matrix_of_free_coordinates = None
        self.deformation_multiplier = 0


    def MoveInnerNodesWithRBF(self):
        # first loop, ONLY ENTERED ONCE
        if self.matrix_of_free_coordinates is None:
            x_original = []
            y_original = []
            for node in self.model.GetModelPart("FluidModelPart").Nodes:
                if not node.IsFixed(KratosMultiphysics.MESH_DISPLACEMENT_X):
                    x_original.append(node.X0)
                if not node.IsFixed(KratosMultiphysics.MESH_DISPLACEMENT_Y):
                    y_original.append(node.Y0)
            x_original = np.array(x_original).reshape(-1,1)
            y_original = np.array(y_original).reshape(-1,1)
            self.matrix_of_free_coordinates = np.c_[x_original, y_original, np.ones((y_original.shape[0],1))]
        self.matrix_of_modified_coordinates = self.rbf(self.matrix_of_free_coordinates)

        # second loop
        i = 0
        for node in self.model.GetModelPart("FluidModelPart").Nodes:
            if not node.IsFixed(KratosMultiphysics.MESH_DISPLACEMENT_X):
                x_disp = self.matrix_of_modified_coordinates[i,0] - node.X0
                node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_X,0, x_disp )
            if not node.IsFixed(KratosMultiphysics.MESH_DISPLACEMENT_Y):
                y_disp = self.matrix_of_modified_coordinates[i,1] - node.Y0
                node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_Y,0, y_disp )
                i +=1
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_X)
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_Y)


    def StoreBifurcationData(self):
        node =  self.model.GetModelPart("FluidModelPart").GetNode(self.control_point)
        self.velocity_y_at_control_point.append(node.GetSolutionStepValue(KratosMultiphysics.VELOCITY_Y))
        self.deformation_multiplier_list.append(self.deformation_multiplier)
        node_up = self.model.GetModelPart("FluidModelPart").GetNode(self.node_up)
        node_down = self.model.GetModelPart("FluidModelPart").GetNode(self.node_down)
        self.narrowing_width.append(node_up.Y - node_down.Y)



    def ModifyInitialGeometry(self):
        super().ModifyInitialGeometry()
        self.IdentifyNodes()
        self.SetUpFreeFormDeformation()



    def IdentifyNodes(self):
        control_down = self.model.GetModelPart("FluidModelPart.GENERIC_ControlDown")
        control_up = self.model.GetModelPart("FluidModelPart.GENERIC_ControlUp")
        fixed_walls= self.model.GetModelPart("FluidModelPart.GENERIC_FixedWalls")

        number_of_nodes_walls = fixed_walls.NumberOfNodes()
        number_of_nodes_down = control_down.NumberOfNodes()
        number_of_nodes_up = control_down.NumberOfNodes()

        #get matrix of original coordinates
        walls_coordinates = np.ones((int(number_of_nodes_walls),3))
        up_coordinates = np.ones((int(number_of_nodes_up),3))
        down_coordinates = np.ones((int(number_of_nodes_down),3))

        counter = 0
        for node in control_down.Nodes:
            down_coordinates[counter, 0] = node.X0
            down_coordinates[counter, 1] = node.Y0
            counter+=1

        counter = 0
        for node in control_up.Nodes:
            up_coordinates[counter, 0] = node.X0
            up_coordinates[counter, 1] = node.Y0
            counter+=1

        counter = 0
        for node in fixed_walls.Nodes:
            walls_coordinates[counter, 0] = node.X0
            walls_coordinates[counter, 1] = node.Y0
            counter+=1

        self.walls = walls_coordinates

        self.up = up_coordinates
        at_3_y = np.where(self.up[:,1] == 3)
        self.up = np.delete(self.up,at_3_y, 0)

        self.down = down_coordinates
        at_0_y = np.where(self.down[:,1] == 0)
        self.down = np.delete(self.down,at_0_y, 0)

        self.fixed_coordinates = np.r_[walls_coordinates, self.down, self.up]




    def SetUpFreeFormDeformation(self):
        #creating a free form deformation object for each control domain
        self.ffd_up = FFD([2,5,2])  #3D box of control points
        self.ffd_down = FFD([2,5,2])  #3D box of control points

        #setting the centre and size of the upper box of control points
        self.ffd_down.box_origin = np.array([1.25, 0, 0.5])
        self.ffd_down.box_length = np.array([1, 1.25, 1])

        #setting the centre and size of the lower box of control points
        self.ffd_up.box_origin = np.array([1.25, 1.75, 0.5])
        self.ffd_up.box_length = np.array([1, 1.25, 1])

        self.list_of_ffds = [self.ffd_up, self.ffd_down]




    def MoveControlPoints(self, scale_of_deformation=1):

        self.ffd_down.array_mu_x[0, 0, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_x[0, 1, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[0, 2, 0] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_down.array_mu_x[0, 3, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[0, 4, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_x[0, 0, 1] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_x[0, 1, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[0, 2, 1] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_down.array_mu_x[0, 3, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[0, 4, 1] = self.deformation_multiplier*scale_of_deformation * 0.0

        self.ffd_down.array_mu_y[0, 0, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_y[0, 1, 0] = self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_down.array_mu_y[0, 2, 0] = self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_down.array_mu_y[0, 3, 0] = self.deformation_multiplier*scale_of_deformation * 0.02
        self.ffd_down.array_mu_y[0, 4, 0] = self.deformation_multiplier*scale_of_deformation * 0.025
        self.ffd_down.array_mu_y[0, 0, 1] = self.deformation_multiplier*scale_of_deformation * 0.00
        self.ffd_down.array_mu_y[0, 1, 1] = self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_down.array_mu_y[0, 2, 1] = self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_down.array_mu_y[0, 3, 1] = self.deformation_multiplier*scale_of_deformation * 0.02
        self.ffd_down.array_mu_y[0, 4, 1] = self.deformation_multiplier*scale_of_deformation * 0.025


        self.ffd_down.array_mu_x[1, 0, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_x[1, 1, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[1, 2, 0] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_down.array_mu_x[1, 3, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[1, 4, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_x[1, 0, 1] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_x[1, 1, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[1, 2, 1] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_down.array_mu_x[1, 3, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_down.array_mu_x[1, 4, 1] = self.deformation_multiplier*scale_of_deformation * 0.0

        self.ffd_down.array_mu_y[1, 0, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_down.array_mu_y[1, 1, 0] = self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_down.array_mu_y[1, 2, 0] = self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_down.array_mu_y[1, 3, 0] = self.deformation_multiplier*scale_of_deformation * 0.02
        self.ffd_down.array_mu_y[1, 4, 0] = self.deformation_multiplier*scale_of_deformation * 0.025
        self.ffd_down.array_mu_y[1, 0, 1] = self.deformation_multiplier*scale_of_deformation * 0.00
        self.ffd_down.array_mu_y[1, 1, 1] = self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_down.array_mu_y[1, 2, 1] = self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_down.array_mu_y[1, 3, 1] = self.deformation_multiplier*scale_of_deformation * 0.02
        self.ffd_down.array_mu_y[1, 4, 1] = self.deformation_multiplier*scale_of_deformation * 0.025

        self.ffd_up.array_mu_x[0, 0, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_up.array_mu_x[0, 1, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[0, 2, 0] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_up.array_mu_x[0, 3, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[0, 4, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_up.array_mu_x[0, 0, 1] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_up.array_mu_x[0, 1, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[0, 2, 1] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_up.array_mu_x[0, 3, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[0, 4, 1] = self.deformation_multiplier*scale_of_deformation * 0.0

        self.ffd_up.array_mu_y[0, 0, 0] = -self.deformation_multiplier*scale_of_deformation * 0.025
        self.ffd_up.array_mu_y[0, 1, 0] = -self.deformation_multiplier*scale_of_deformation * 0.020
        self.ffd_up.array_mu_y[0, 2, 0] = -self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_up.array_mu_y[0, 3, 0] = -self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_up.array_mu_y[0, 4, 0] = -self.deformation_multiplier*scale_of_deformation * 0.00
        self.ffd_up.array_mu_y[0, 0, 1] = -self.deformation_multiplier*scale_of_deformation * 0.025
        self.ffd_up.array_mu_y[0, 1, 1] = -self.deformation_multiplier*scale_of_deformation * 0.020
        self.ffd_up.array_mu_y[0, 2, 1] = -self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_up.array_mu_y[0, 3, 1] = -self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_up.array_mu_y[0, 4, 1] = -self.deformation_multiplier*scale_of_deformation * 0.00


        self.ffd_up.array_mu_x[1, 0, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_up.array_mu_x[1, 1, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[1, 2, 0] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_up.array_mu_x[1, 3, 0] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[1, 4, 0] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_up.array_mu_x[1, 0, 1] = self.deformation_multiplier*scale_of_deformation * 0.0
        self.ffd_up.array_mu_x[1, 1, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[1, 2, 1] = self.deformation_multiplier*scale_of_deformation * 0.06
        self.ffd_up.array_mu_x[1, 3, 1] = self.deformation_multiplier*scale_of_deformation * 0.04
        self.ffd_up.array_mu_x[1, 4, 1] = self.deformation_multiplier*scale_of_deformation * 0.0

        self.ffd_up.array_mu_y[1, 0, 0] = -self.deformation_multiplier*scale_of_deformation * 0.025
        self.ffd_up.array_mu_y[1, 1, 0] = -self.deformation_multiplier*scale_of_deformation * 0.020
        self.ffd_up.array_mu_y[1, 2, 0] = -self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_up.array_mu_y[1, 3, 0] = -self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_up.array_mu_y[1, 4, 0] = -self.deformation_multiplier*scale_of_deformation * 0.00
        self.ffd_up.array_mu_y[1, 0, 1] = -self.deformation_multiplier*scale_of_deformation * 0.025
        self.ffd_up.array_mu_y[1, 1, 1] = -self.deformation_multiplier*scale_of_deformation * 0.020
        self.ffd_up.array_mu_y[1, 2, 1] = -self.deformation_multiplier*scale_of_deformation * 0.015
        self.ffd_up.array_mu_y[1, 3, 1] = -self.deformation_multiplier*scale_of_deformation * 0.01
        self.ffd_up.array_mu_y[1, 4, 1] = -self.deformation_multiplier*scale_of_deformation * 0.00

        moved_up = self.ffd_up(self.up)
        moved_down = self.ffd_down(self.down)


        #Moving lower part
        control_down = self.model.GetModelPart("FluidModelPart.GENERIC_ControlDown")
        i=0
        for node in control_down.Nodes:
            if node.Y0 != 0:
                x_disp = moved_down[i,0] - node.X0
                y_disp = moved_down[i,1] - node.Y0
                node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_X,0, x_disp )
                node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_Y,0, y_disp )
                node.X = node.X0 + x_disp
                node.Y = node.Y0 + y_disp
                i +=1
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_X)
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_Y)


        #moving upper part
        control_up = self.model.GetModelPart("FluidModelPart.GENERIC_ControlUp")
        i=0
        for node in control_up.Nodes:
            if node.Y0 != 3:
                x_disp = moved_up[i,0] - node.X0
                y_disp = moved_up[i,1] - node.Y0
                node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_X,0, x_disp )
                node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_Y,0, y_disp )
                node.X = node.X0 + x_disp
                node.Y = node.Y0 + y_disp
                i +=1
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_X)
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_Y)

        self.moved_coordinates =  np.r_[self.walls, moved_down, moved_up]



    def UpdateRBF(self):
        self.rbf = RBF(original_control_points = self.fixed_coordinates, deformed_control_points =
            self.moved_coordinates, radius=0.75)


    def LockOuterWalls(self):
        for node in self.model.GetModelPart("FluidModelPart.GENERIC_FixedWalls").Nodes:
            node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_X,0, 0 )
            node.SetSolutionStepValue(KratosMultiphysics.MESH_DISPLACEMENT_Y,0, 0)
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_X)
            node.Fix(KratosMultiphysics.MESH_DISPLACEMENT_Y)



    def UpdateDeformationMultiplier(self):
        ####Train trajectory####
        if self.time>10.0 and self.time<=21.0: # start modifying narrowing from 10 seconds onwards
            self.deformation_multiplier+=self.delta_deformation
            if self.deformation_multiplier > self.maximum:
                self.deformation_multiplier = self.maximum
        elif self.time>31.0 and self.time<41.9: # start modifying narrowing from 10 seconds onwards
            self.deformation_multiplier-=self.delta_deformation
            if self.deformation_multiplier < self.minimum:
                self.deformation_multiplier = self.minimum






    def InitializeSolutionStep(self):
        super().InitializeSolutionStep()

        #free all nodes
        for node in self.model.GetModelPart("FluidModelPart").Nodes:
            node.Free(KratosMultiphysics.MESH_DISPLACEMENT_X)
            node.Free(KratosMultiphysics.MESH_DISPLACEMENT_Y)

        self.UpdateDeformationMultiplier()
        self.MoveControlPoints()
        self.LockOuterWalls()





    def FinalizeSolutionStep(self):
        super().FinalizeSolutionStep()
        self.StoreBifurcationData()

        ArrayOfResults = []
        for node in self._GetSolver().fluid_solver.GetComputingModelPart().Nodes:
            ArrayOfResults.append(node.GetSolutionStepValue(KratosMultiphysics.VELOCITY_X, 0))
            ArrayOfResults.append(node.GetSolutionStepValue(KratosMultiphysics.VELOCITY_Y, 0))
            ArrayOfResults.append(node.GetSolutionStepValue(KratosMultiphysics.PRESSURE, 0))
        self.time_step_solution_container.append(ArrayOfResults)




    def GetBifuracationData(self):
        return self.velocity_y_at_control_point ,  self.narrowing_width, self.deformation_multiplier_list



    def GetSnapshotsMatrix(self):
        SnapshotMatrix = np.zeros((len(self.time_step_solution_container[0]), len(self.time_step_solution_container)))
        for i in range(len(self.time_step_solution_container)):
            Snapshot_i= np.array(self.time_step_solution_container[i])
            SnapshotMatrix[:,i] = Snapshot_i.transpose()
        return SnapshotMatrix








def prepare_files(working_path):
    """pre-pending the absolut path of the files in the Project Parameters"""
    with open(working_path+'/ProblemFiles/ProjectParameters.json','r') as f:
        updated_project_parameters = json.load(f)
        file_input_name = updated_project_parameters["solver_settings"]["fluid_solver_settings"]["model_import_settings"]["input_filename"]
        materials_filename = updated_project_parameters["solver_settings"]["fluid_solver_settings"]["material_import_settings"]["materials_filename"]
        gid_output_name = updated_project_parameters["output_processes"]["gid_output"][0]["Parameters"]["output_name"]

        updated_project_parameters["solver_settings"]["fluid_solver_settings"]["model_import_settings"]["input_filename"] = working_path + '/ProblemFiles/'+ file_input_name
        updated_project_parameters["solver_settings"]["fluid_solver_settings"]["material_import_settings"]["materials_filename"] = working_path +'/ProblemFiles/'+ materials_filename
        updated_project_parameters["output_processes"]["gid_output"][0]["Parameters"]["output_name"] = working_path +'/Results/FOM'

    with open(working_path+'/ProblemFiles/ProjectParameters_modified.json','w') as f:
        json.dump(updated_project_parameters, f, indent = 4)









def convert_to_nd(SnapshotsMatrix, number_of_dimensions=2):
    for i in range(np.shape(SnapshotsMatrix)[1]):
        column_mean = np.mean( SnapshotsMatrix[:,i].reshape(-1,number_of_dimensions).reshape(-1,number_of_dimensions),0).reshape(-1,1)
        if i ==0:
            columns_means = column_mean
        else:
            columns_means = np.c_[columns_means,column_mean]

    return columns_means




























def Train_ROM():

    if not os.path.exists(f'./Results/FOM.post.bin'):

        with open("ProblemFiles/ProjectParameters_modified.json", 'r') as parameter_file:
            parameters = KratosMultiphysics.Parameters(parameter_file.read())
        global_model = KratosMultiphysics.Model()
        simulation = FOM_Class(global_model, parameters)
        simulation.Run()
        SnapshotsMatrix = simulation.GetSnapshotsMatrix()
        velocity_y, narrowing, deformation_multiplier = simulation.GetBifuracationData()
        #reynolds = simulation.GetReynoldsData()
        #np.save('Results/reynolds.npy', reynolds)
        np.save('Results/deformation_multiplier.npy', deformation_multiplier)
        np.save('Results/narrowing.npy', narrowing)
        np.save('Results/Velocity_y.npy', velocity_y)
        np.save('Results/SnapshotMatrix.npy', SnapshotsMatrix )

























if __name__=="__main__":
    #library for passing arguments to the script from bash
    from sys import argv

    working_path = argv[1]

    prepare_files(working_path)


    Train_ROM()
