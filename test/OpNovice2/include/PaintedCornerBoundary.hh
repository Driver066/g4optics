#ifndef PaintedCornerBoundary_h
#define PaintedCornerBoundary_h 1
#include "G4OpBoundaryProcess.hh"
#include "G4OpticalPhysics.hh"
#include "G4AffineTransform.hh"
#include <map>
class DetectorConstruction;
class G4VPhysicalVolume;

class PaintedCornerBoundary final : public G4OpBoundaryProcess {
 public:
  explicit PaintedCornerBoundary(const DetectorConstruction* detector);
  G4VParticleChange* PostStepDoIt(const G4Track&, const G4Step&) override;
 private:
  struct Pending {
    G4int step;
    G4VPhysicalVolume* tile;
    G4AffineTransform toLocal;
    G4ThreeVector halfSize;
    G4int faces;
  };
  const DetectorConstruction* fDetector;
  G4int fEvent=-1;
  std::map<G4int, Pending> fPending;
  std::map<G4int, G4int> fLastCorrection;
};

class StackOpticalPhysics final : public G4OpticalPhysics {
 public:
  explicit StackOpticalPhysics(const DetectorConstruction* detector) : fDetector(detector) {}
 protected:
  void ConstructProcess() override;
 private:
  const DetectorConstruction* fDetector;
};
#endif
