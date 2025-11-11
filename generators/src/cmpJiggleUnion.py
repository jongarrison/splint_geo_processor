
from Rhino.Geometry import Brep, Vector3d, BoundingBox, Point3d
import clr #see: https://discourse.mcneel.com/t/activating-additional-out-parameters-rhinocommon-python/149516
import Rhino
import System
import random
from Rhino.Geometry import VolumeMassProperties
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from System.Collections.Generic import IEnumerable

class cmpJiggleUnion:
    def __init__(self, breps: List[Brep], jiggleToleranceVector: Vector3d, maxAttempts: int, enableJiggle: bool):
        #inputs
        # breps - breps to union together (Python list, converted to IEnumerable internally by .NET)
        # jiggleToleranceVector - 3D vector specifying the amount of allowed motion in a single jiggle in each direction
        # maxAttempts - How many times to loop through the whole list of breps nudging them randomly
        # enableJiggle - sometimes for development just doing the normal union and returning is desirable

        print(f"jiggle {maxAttempts=} X={jiggleToleranceVector.X}, Y={jiggleToleranceVector.Y}, Z={jiggleToleranceVector.Z}")

        self.breps = breps  # Python list - automatically converted to IEnumerable by pythonnet
        self.jiggleToleranceVector = jiggleToleranceVector
        self.maxAttempts = maxAttempts
        self.enableJiggle = enableJiggle

        self.tol: float = 0.1
        self.allowNonManifold: bool = False
        self.toggleJigglePositive: bool = True

        #jiggleVector, is a 3D vector with each component describing the amount of plus or minus jiggle allowed in each attempt to get the
        # union to work. Union is first attempted without any jiggle and the hope is that works fine

        # Is this a kluge? I'll let you decide. We're trying to make a Boolean Union work for all situations across a range.
        # Because of the randomness in the jiggle, multiple iterations may need to happen to get a result


    def getRandomJiggle(self, tolerance):
        self.toggleJigglePositive = not self.toggleJigglePositive
        dir = 1 if self.toggleJigglePositive else -1
        return random.uniform(0.0, dir * tolerance)
        # return random.uniform(-tolerance, tolerance)

    def randomNudge(self, geo):
        xJiggle = self.getRandomJiggle(self.jiggleToleranceVector.X)
        yJiggle = self.getRandomJiggle(self.jiggleToleranceVector.Y)
        zJiggle = self.getRandomJiggle(self.jiggleToleranceVector.Z)
        print(f"nudge x={xJiggle} y={yJiggle} z={zJiggle}")
        geo.Transform(Rhino.Geometry.Transform.Translation(xJiggle, yJiggle, zJiggle))

    def generatePointsInsideBrep(self, inputBrep: Brep, pointCount: int):
        # Use .Overloads to select specific method signature (False = world coordinates)
        bb: BoundingBox = inputBrep.GetBoundingBox.Overloads[bool](False)  # type: ignore[attr-defined]

        resultPts = []
        attempts = 0
        maxAttempts = 10 * pointCount

        while len(resultPts) < pointCount:
            attempts += 1
            randPt = Point3d(random.uniform(bb.Min.X, bb.Max.X), random.uniform(bb.Min.Y, bb.Max.Y), random.uniform(bb.Min.Z, bb.Max.Z))
            if inputBrep.IsPointInside(randPt, self.tol, True):
                resultPts.append(randPt)
            if attempts > maxAttempts:
                raise Exception(f"Unable to generate {pointCount} points inside geometry in {attempts} attempts. Generated {len(resultPts)} points successfully")

        print(f"Generated {len(resultPts)} points successfully inside brep in {attempts} attempts. ")
        return resultPts

    def isAcceptableResult(self, unionedBreps, interiorPoints: List[Point3d]):
        if unionedBreps is None:
            print("isAcceptableResult result='No BREP'")
            return False
        elif len(unionedBreps) == 1:
            testBrep: Brep = unionedBreps[0]
            #So far so good, now test the interior points
            for pt in interiorPoints:
                if not testBrep.IsPointInside(pt, self.tol, False):
                    print("isAcceptableResult result='Failed Point Check'")
                    return False

            print("isAcceptableResult result='Success'")
            return True
        else:
            print("isAcceptableResult result='Multiple BREPs'")
            return False

    def doUnionRun(self):
        try:
            print(f"received {len(self.breps)} input(s)")
            testPointCount = 400

            testPts = []
            for bp in self.breps:
                testPts.extend(self.generatePointsInsideBrep(bp, testPointCount))

            # pythonnet automatically converts Python List[Brep] to .NET IEnumerable
            unionedBrep = Brep.CreateBooleanUnion(self.breps, self.tol, self.allowNonManifold)  # type: ignore[arg-type]
            
            unionAttempt = 0
            print(f"{unionAttempt=} type={type(unionedBrep)}")

            if self.isAcceptableResult(unionedBrep, testPts):
                print(f"Successful Union on first attempt!")
            else:
                isGoodResultFound = False
                for desperationFactor in range(0, self.maxAttempts):
                    for inBrep in self.breps:
                        unionAttempt += 1
                        self.randomNudge(inBrep)
                        # pythonnet automatically converts Python List[Brep] to .NET IEnumerable
                        unionedBrep = Brep.CreateBooleanUnion(self.breps, self.tol, self.allowNonManifold)  # type: ignore[arg-type]
                        if self.isAcceptableResult(unionedBrep, testPts):
                            print(f"SUCCESS: {unionAttempt=} type={type(unionedBrep)} {desperationFactor=}")
                            isGoodResultFound = True
                            break
                        else:
                            print(f"FAIL: {unionAttempt=} type={type(unionedBrep)} {desperationFactor=}")

                    if isGoodResultFound:
                        break

            jiggleCount = unionAttempt

            if unionedBrep is None:
                raise Exception("Unable to boolean provided breps")

            resultBrep = unionedBrep[0]
            print(f"Success out={type(resultBrep)}")

            # Use .Overloads to select the Compute(Brep) overload
            volProps = VolumeMassProperties.Compute.Overloads[Brep](resultBrep)  # type: ignore[attr-defined]
            print(f"{volProps=}")
            if volProps is None:
                msg = "Invalid brep generated, could not compute volume"
                print(msg)
                raise Exception(msg)


            return resultBrep, jiggleCount, volProps.Volume, testPts
        except Exception as exc:
            print(exc)
            raise exc

    def run(self):
        # This is the main function to call to do the jiggle union

        if self.enableJiggle == False:
            return Brep.CreateBooleanUnion(self.breps, self.tol, self.allowNonManifold), -1, []


        unions = []
        jigCounts = []
        vols = []
        testPtsLists = []


        # Do multiple runs to check agreement
        for i in range (0, 4):
            try:
                un, jig, vol, testPts = self.doUnionRun();
                unions.append(un)
                jigCounts.append(jig)
                vols.append(vol)
                testPtsLists.append(testPts)
                print(f"Union generation attempt {i=} succeeded")
                if len(unions) > 1:
                    break
            except:
                print(f"Union generation attempt {i=} failed")
                pass

        if len(unions) != 2:
            raise Exception("Boolean Union not possible after repeated attempts")

        print(f"Result volumes {vols[0]=} {vols[1]=}")
        iResult = 0 if (vols[0] > vols[1]) else 1
        
        unionedBrep = unions[iResult]
        jiggleCount = jigCounts[iResult]
        testedPoints = testPtsLists[iResult]

        return unionedBrep, jiggleCount, testedPoints    


        #another way to do boolean union, that may help in some situations:
        # nakedEdgePoints = clr.StrongBox[System.Array[Rhino.Geometry.Point3d]]()
        # badIntersectionPoints = clr.StrongBox[System.Array[Rhino.Geometry.Point3d]]()
        # nonManifoldEdgePoints = clr.StrongBox[System.Array[Rhino.Geometry.Point3d]]()

        # #see: https://developer.rhino3d.com/api/rhinocommon/rhino.geometry.brep/createbooleanunion#(ienumerable%3Cbrep%3E,double,boolean)
        # unionedBrep = Brep.CreateBooleanUnion(breps, tol, allowNonManifold, nakedEdgePoints, badIntersectionPoints, nonManifoldEdgePoints)
        # print(f"nep={type(nakedEdgePoints)} bip={type(badIntersectionPoints)} nep={type(nonManifoldEdgePoints)}")
