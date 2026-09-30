#ifndef OpticalNumerics_h
#define OpticalNumerics_h 1

#include "globals.hh"
#include "G4ThreeVector.hh"
#include <fstream>
#include <map>
#include <memory>
#include <vector>

class G4Event;
class G4Step;
class G4Track;
class G4GenericMessenger;
class G4OpBoundaryProcess;
class G4VPhysicalVolume;
class DetectorConstruction;

// Serial v2 numerical profile and passive audit. Never owned by TrackInformation.
class OpticalNumerics {
 public:
  static OpticalNumerics& Instance();
  void InstallMessenger();
  const G4String& Mode() const { return fMode; }
  G4int Scale() const { return fScale; }
  G4bool IsProbe() const { return !fProbeFile.empty(); }
  G4bool Enabled() const { return fEnabled; }
  void Validate(const DetectorConstruction&) const;
  void BeginRun(const G4String& output);
  void EndRun();
  G4bool GenerateProbe(G4Event*);
  void BeginEvent(const G4Event*);
  void EndEvent();
  void ObserveStep(const G4Step*);
  void EndTracking(const G4Track*);
  void Correction(const G4Track&, const G4Step&, G4int reflectionStep,
                  const G4ThreeVector& before, const G4ThreeVector& after,
                  G4int faces, G4int tile, G4double tolerance);
  void ImmediateCorrection(const G4Track&,const G4Step&,const G4ThreeVector&,G4int,G4double);
  void WriteIdentity(std::ostream&) const;
  static void Fail(const G4String&);
 private:
  struct Probe { G4String id; long seed[3]; G4ThreeVector position, direction, polarization; G4double energy; };
  struct Trace { G4int edgeStep=-10; G4int tile=-1; G4int sensor=-1; G4int status=-1;
                 G4String process, volume; G4int copy=-1; G4bool logged=false; };
  struct Immediate { G4int step,faces; G4double tolerance; G4ThreeVector before,after;
                     G4VPhysicalVolume* tile; G4VPhysicalVolume* rawPost; };
  G4String fMode="legacy", fProbeFile;
  G4int fScale=0;
  std::unique_ptr<G4GenericMessenger> fMessenger;
  std::vector<Probe> fProbes;
  std::map<G4int, Trace> fTracks;
  std::map<G4int, Immediate> fImmediate;
  std::ofstream fOutput;
  const DetectorConstruction* fDetector=nullptr;
  G4OpBoundaryProcess* fBoundary=nullptr;
  G4bool fEnabled=false;
  G4int fEvent=-1, fCorrections=0, fNoRindex=0, fEscapes=0;
};
#endif
