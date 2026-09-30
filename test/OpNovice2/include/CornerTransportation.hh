#ifndef CornerTransportation_h
#define CornerTransportation_h 1
#include "G4Transportation.hh"
#include "G4VParticleChange.hh"

// Optical photons only, in the opt-in v2 numerical profile. All ordinary
// transport methods remain the installed Geant4 implementation.
class CornerTransportation final : public G4Transportation {
 public:
  explicit CornerTransportation(G4int verbosity) : G4Transportation(verbosity) {}
  void AdoptRelocation(const G4TouchableHandle&, const G4ThreeVector&);
  G4VPhysicalVolume* CachedVolume() const { return fCurrentTouchableHandle->GetVolume(); }
};

// Compose the stock boundary ParticleChange with the stock transport geometry
// ParticleChange. This avoids copying (or reimplementing) any physical proposal.
class CornerParticleChange final : public G4VParticleChange {
 public:
  void Prepare(const G4Track&, const G4Step&, G4VParticleChange*, const G4TouchableHandle&);
  G4Step* UpdateStepForPostStep(G4Step*) override;
 private:
  G4VParticleChange* fBoundary=nullptr;
  G4ParticleChangeForTransport fGeometry;
};
#endif
