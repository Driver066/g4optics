//
// ********************************************************************
// * License and Disclaimer                                           *
// *                                                                  *
// * The  Geant4 software  is  copyright of the Copyright Holders  of *
// * the Geant4 Collaboration.  It is provided  under  the terms  and *
// * conditions of the Geant4 Software License,  included in the file *
// * LICENSE and available at  http://cern.ch/geant4/license .  These *
// * include a list of copyright holders.                             *
// *                                                                  *
// * Neither the authors of this software system, nor their employing *
// * institutes,nor the agencies providing financial support for this *
// * work  make  any representation or  warranty, express or implied, *
// * regarding  this  software system or assume any liability for its *
// * use.  Please see the license in the file  LICENSE  and URL above *
// * for the full disclaimer and the limitation of liability.         *
// *                                                                  *
// * This  code  implementation is the result of  the  scientific and *
// * technical work of the GEANT4 collaboration.                      *
// * By using,  copying,  modifying or  distributing the software (or *
// * any work based  on the software)  you  agree  to acknowledge its *
// * use  in  resulting  scientific  publications,  and indicate your *
// * acceptance of all terms of the Geant4 Software license.          *
// ********************************************************************
//
/// \file optical/OpNovice2/src/DetectorConstruction.cc
/// \brief Implementation of the DetectorConstruction class
//
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "DetectorConstruction.hh"

#include "DetectorMessenger.hh"

#include "G4Box.hh"
#include "G4Element.hh"
#include "G4IntersectionSolid.hh"
#include "G4LogicalBorderSurface.hh"
#include "G4LogicalSkinSurface.hh"
#include "G4LogicalVolume.hh"
#include "G4Material.hh"
#include "G4NistManager.hh"
#include "G4OpticalSurface.hh"
#include "G4PVPlacement.hh"
#include "G4PhysicalConstants.hh"
#include "G4Sphere.hh"
#include "G4SubtractionSolid.hh"
#include "G4UnionSolid.hh"
#include "G4SystemOfUnits.hh"
#include "G4ThreeVector.hh"
#include "G4VSolid.hh"

#include "G4Colour.hh"
#include "G4VisAttributes.hh"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <vector>
#include <string>

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorConstruction::DetectorConstruction()
  : G4VUserDetectorConstruction(), fDetectorMessenger(nullptr)
{
  fTankMPT = new G4MaterialPropertiesTable();
  fWorldMPT = new G4MaterialPropertiesTable();
  fSurfaceMPT = new G4MaterialPropertiesTable();
  // The properties table of SiPM
  fSiPMMPT = new G4MaterialPropertiesTable();
  fGreaseMPT = new G4MaterialPropertiesTable();

  fSurface = new G4OpticalSurface("Surface");
  fSurface->SetType(dielectric_dielectric);
  fSurface->SetFinish(ground);
  fSurface->SetModel(unified);
  fSurface->SetMaterialPropertiesTable(fSurfaceMPT);

  auto nist = G4NistManager::Instance();
  fTankMaterial = nist->FindOrBuildMaterial("G4_PLASTIC_SC_VINYLTOLUENE");
  fWorldMaterial = nist->FindOrBuildMaterial("G4_AIR");

  // SAE 304 stainless steel baseline used by the realistic-neutron study.
  // Keep the study composition explicit rather than relying on a Geant4
  // material alias whose alloy fractions may differ across releases.
  fAbsorberMaterial = new G4Material("StainlessSteelSAE304", 7.9 * g / cm3, 3);
  fAbsorberMaterial->AddElement(nist->FindOrBuildElement("Fe"), 0.74);
  fAbsorberMaterial->AddElement(nist->FindOrBuildElement("Cr"), 0.18);
  fAbsorberMaterial->AddElement(nist->FindOrBuildElement("Ni"), 0.08);

  // The material of SiPM
  fSiPMMaterial = nist->FindOrBuildMaterial("G4_Si");

  // EJ-550 optical-grade silicone grease is represented with a simple
  // silicone-like composition proxy. Eljen specifies a specific gravity of
  // 1.06; optical constants are supplied by macro.
  fGreaseMaterial = new G4Material("EJ550_Grease", 1.06 * g / cm3, 4);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("C"), 2);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("H"), 6);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("O"), 1);
  fGreaseMaterial->AddElement(nist->FindOrBuildElement("Si"), 1);

  fDetectorMessenger = new DetectorMessenger(this);

  // ----- SiPM -----
  const G4int nEntries = 2;
  G4double photonEnergy[nEntries] = {2.0 * eV, 3.3 * eV};

  // Toy SiPM/silicon optical properties.
  // This is enough for first-pass photon entry + detection.
  G4double siRIndex[nEntries] = {4.0, 4.0};

  // Strong absorption once photons enter SiPM.
  G4double siAbsLength[nEntries] = {1.0 * um, 1.0 * um};

  fSiPMMPT->AddProperty("RINDEX", photonEnergy, siRIndex, nEntries);
  fSiPMMPT->AddProperty("ABSLENGTH", photonEnergy, siAbsLength, nEntries);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorConstruction::~DetectorConstruction()
{
  delete fTankMPT;
  delete fWorldMPT;
  delete fSurfaceMPT;
  // Frees the memory of SiPM
  delete fSiPMMPT;
  delete fGreaseMPT;
  delete fSurface;
  delete fDetectorMessenger;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

G4VPhysicalVolume* DetectorConstruction::Construct()
{
  fTank = nullptr;
  fAbsorber = nullptr;
  fAbsorber_LV = nullptr;
  fTanks.clear();
  fAbsorbers.clear();
  fSiPM = nullptr;
  fSiPMs.clear();
  fGrease = nullptr;
  fGrease_LV = nullptr;

  if (fDimpleFrontCompensation && !fDimpleEnabled) {
    G4Exception("DetectorConstruction::Construct", "OpNovice2_Issue9_007",
                FatalException, "frontCompensation requires dimple/enabled true.");
  }
  if (fIssue9GeometryAudit || (fDimpleEnabled &&
      (fDimpleMode == "cube" || fDimpleFrontCompensation))) {
    ValidateIssue9GeometryConfiguration();
  }

  if (fStackLayoutStudy && !fStackEnabled) {
    G4Exception("DetectorConstruction::Construct", "OpNovice2_LayoutStudy_001",
                FatalException, "stack/layoutStudy requires stack/enabled true.");
  }
  if (fStackEnabled) {
    ValidateStackConfiguration();
  }
  if (IsStackLayoutStudy()) {
    G4cout << "Stack layout study: layout=" << fSiPMLayout
           << ", layers=" << fStackLayers
           << ", internal_gaps=9, readout_gap=" << GetStackReadoutGap() / mm
           << " mm, core_length=" << GetStackLength() / mm
           << " mm, required_source_z=" << (0.5 * GetStackLength() + 1.5 * mm) / mm
           << " mm, sensor_stride=" << GetStackSensorStride()
           << ", sensors_per_layer=" << GetStackSensorsPerLayer() << G4endl;
  }

  fTankMaterial->SetMaterialPropertiesTable(fTankMPT);
  fTankMaterial->GetIonisation()->SetBirksConstant(0.126 * mm / MeV);

  fWorldMaterial->SetMaterialPropertiesTable(fWorldMPT);

  // SiPM Properties Table
  fSiPMMaterial->SetMaterialPropertiesTable(fSiPMMPT);
  fGreaseMaterial->SetMaterialPropertiesTable(fGreaseMPT);

  // ------------- Volumes --------------
  // The experimental Hall
  auto world_box = new G4Box("World", fExpHall_x, fExpHall_y, fExpHall_z);

  fWorld_LV = new G4LogicalVolume(world_box, fWorldMaterial, "World");

  G4VPhysicalVolume* world_PV =
    new G4PVPlacement(nullptr, G4ThreeVector(), fWorld_LV, "World", nullptr, false, 0);

  // The tank
  auto tank_box = new G4Box("Tank_Box", fTank_x, fTank_y, fTank_z);
  G4VSolid* tank_solid = tank_box;

  if (fBottomCavityEnabled && fDimpleEnabled) {
    G4ExceptionDescription msg;
    msg << "/opnovice2/dimple/enabled cannot be combined with "
        << "/opnovice2/tank/bottomCavity true. Use one cavity model at a time.";
    G4Exception("DetectorConstruction::Construct",
                "OpNovice2_Dimple_001",
                FatalException,
                msg);
  }

  if (fBottomCavityEnabled) {
    auto bottomCavitySphere = new G4Sphere("Tank_BottomCavitySphere",
                                           0.,
                                           GetBottomCavityRadius(),
                                           0.,
                                           360. * deg,
                                           0.,
                                           180. * deg);

    tank_solid = new G4SubtractionSolid("Tank",
                                        tank_box,
                                        bottomCavitySphere,
                                        nullptr,
                                        G4ThreeVector(0., 0., -fTank_z));
  }
  else if (fDimpleEnabled) {
    ValidateDimpleConfiguration();
    G4cout << "Dimple geometry: shape=" << fDimpleMode
           << ", radius=" << fDimpleRadius / mm
           << " mm, sipm_mode=" << GetEffectiveDimpleSiPMMode()
           << ", opening_z=" << -fTank_z / mm << " mm" << G4endl;

    // Build the front extension on the uncut tile, then subtract the rear
    // cavity. This ordering also avoids refilling a cavity in a thin tile.
    G4VSolid* compensatedBase = tank_box;
    const G4double join = std::min({1.e-4 * mm, 0.2 * fTank_z,
                                    0.1 * GetDimpleCubeSide()});
    if (fDimpleFrontCompensation) {
      G4VSolid* frontSolid = nullptr;
      G4double frontCenterZ = fTank_z;
      if (fDimpleMode == "hemisphere") {
        // A complete sphere overlaps the tile; the exposed portion is exactly
        // the hemisphere removed at the back. No face-only union is used.
        frontSolid = new G4Sphere("Tank_FrontSphere", 0., fDimpleRadius,
                                  0., 360. * deg, 0., 180. * deg);
      }
      else {
        const G4double side = GetDimpleCubeSide();
        frontSolid = new G4Box("Tank_FrontCube", 0.5 * side, 0.5 * side,
                               0.5 * (side + join));
        // The join extends inward only; the exposed height remains side.
        frontCenterZ += 0.5 * (side - join);
      }
      compensatedBase = new G4UnionSolid("Tank_CompensatedBase", tank_box,
                                          frontSolid, nullptr,
                                          G4ThreeVector(0., 0., frontCenterZ));
    }

    G4VSolid* cavitySolid = nullptr;
    G4double cavityCenterZ = -fTank_z;
    if (fDimpleMode == "hemisphere") {
      cavitySolid = new G4Sphere("Tank_DimpleSphere", 0., fDimpleRadius,
                                 0., 360. * deg, 0., 180. * deg);
    }
    else {
      const G4double side = GetDimpleCubeSide();
      cavitySolid = new G4Box("Tank_DimpleCube", 0.5 * side, 0.5 * side,
                              0.5 * (side + join));
      // Extend the cutting tool outside the rear face, keeping depth=side.
      cavityCenterZ += 0.5 * (side - join);
    }
    tank_solid = new G4SubtractionSolid("Tank", compensatedBase, cavitySolid,
                                        nullptr, G4ThreeVector(0., 0., cavityCenterZ));
  }

  fTank_LV = new G4LogicalVolume(tank_solid, fTankMaterial, "Tank");

  const auto layerCount = GetStackLayerCount();
  for (G4int layer = 0; layer < layerCount; ++layer) {
    const auto center = fStackEnabled ? GetStackTileCenterZ(layer) : 0.;
    auto placement = new G4PVPlacement(nullptr,
                                       G4ThreeVector(0., 0., center),
                                       fTank_LV,
                                       "Tank",
                                       fWorld_LV,
                                       false,
                                       layer,
                                       fStackEnabled);
    fTanks.push_back(placement);
    if (IsStackLayoutStudy()) {
      G4cout << "Stack tile placement: layer=" << layer
             << ", world=" << placement->GetObjectTranslation() / mm
             << " mm, full_size=" << G4ThreeVector(2.*fTank_x, 2.*fTank_y, 2.*fTank_z) / mm
             << " mm" << G4endl;
    }
    if (layer == 0) {
      fTank = placement;
    }
  }

  if (fAbsorberEnabled) {
    ValidateAbsorberConfiguration();

    auto absorberBox =
      new G4Box("SteelAbsorber_Box", fAbsorber_x, fAbsorber_y, fAbsorber_z);
    fAbsorber_LV =
      new G4LogicalVolume(absorberBox, fAbsorberMaterial, "SteelAbsorber");

    auto absorberVis = new G4VisAttributes(G4Colour(0.45, 0.45, 0.50, 0.75));
    absorberVis->SetForceSolid(true);
    fAbsorber_LV->SetVisAttributes(absorberVis);

    for (G4int layer = 0; layer < layerCount; ++layer) {
      const auto center = fStackEnabled
        ? GetStackSteelCenterZ(layer)
        : GetAbsorberCenterZ();
      auto placement = new G4PVPlacement(nullptr,
                                         G4ThreeVector(0., 0., center),
                                         fAbsorber_LV,
                                         "SteelAbsorber",
                                         fWorld_LV,
                                         false,
                                         layer,
                                         true);
      fAbsorbers.push_back(placement);
      if (IsStackLayoutStudy()) {
        G4cout << "Stack steel placement: layer=" << layer
               << ", world=" << placement->GetObjectTranslation() / mm
               << " mm, full_size=" << GetAbsorberFullSize() / mm << " mm" << G4endl;
      }
      if (layer == 0) {
        fAbsorber = placement;
      }
    }

    G4cout << "Realistic-neutron absorber: material="
           << fAbsorberMaterial->GetName()
           << ", density=" << fAbsorberMaterial->GetDensity() / (g / cm3)
           << " g/cm3, full_size="
           << 2. * fAbsorber_x / mm << " x "
           << 2. * fAbsorber_y / mm << " x "
           << 2. * fAbsorber_z / mm << " mm, center_z="
           << (fStackEnabled ? GetStackSteelCenterZ(0) : GetAbsorberCenterZ()) / mm
           << " mm, tile_gap=0 mm, layers=" << layerCount << G4endl;
  }

  if (fGreaseEnabled) {
    ValidateGreaseConfiguration();

    G4VSolid* greaseSolid = nullptr;
    G4ThreeVector greasePos;

    if (fDimpleEnabled) {
      G4double sipmHx = 0.0;
      G4double sipmHy = 0.0;
      G4double sipmHz = 0.0;
      G4ThreeVector sipmPos;
      ComputeSiPMPlacement(sipmHx, sipmHy, sipmHz, sipmPos);

      const G4double localBottom = sipmPos.z() + sipmHz + fTank_z;
      const G4double clipHz = 0.5 * (fDimpleRadius - localBottom);
      const G4double clipCenterZ = localBottom + clipHz;

      auto greaseSphere = new G4Sphere("Grease_DimpleSphere",
                                        0.,
                                        fDimpleRadius,
                                        0.,
                                        360. * deg,
                                        0.,
                                        180. * deg);
      auto greaseClip = new G4Box("Grease_DimpleClip",
                                   0.5 * GetGreaseActiveU(),
                                   0.5 * GetGreaseActiveV(),
                                   clipHz);
      greaseSolid = new G4IntersectionSolid("Grease_DimpleGap",
                                             greaseSphere,
                                             greaseClip,
                                             nullptr,
                                             G4ThreeVector(0., 0., clipCenterZ));
      greasePos = G4ThreeVector(0., 0., -fTank_z);
    }
    else {
      G4double greaseHx = 0.0;
      G4double greaseHy = 0.0;
      G4double greaseHz = 0.0;
      ComputeGreasePlacement(greaseHx, greaseHy, greaseHz, greasePos);
      greaseSolid = new G4Box("Grease_Box", greaseHx, greaseHy, greaseHz);
    }

    fGrease_LV = new G4LogicalVolume(greaseSolid, fGreaseMaterial, "Grease");

    auto greaseVis = new G4VisAttributes(G4Colour(0.0, 0.8, 1.0, 0.35));
    greaseVis->SetForceSolid(true);
    fGrease_LV->SetVisAttributes(greaseVis);

    fGrease = new G4PVPlacement(nullptr,
                                greasePos,
                                fGrease_LV,
                                "Grease",
                                fWorld_LV,
                                false,
                                0,
                                true);
  }

  // The single- or multi-SiPM steel-module layout.
  ValidateSiPMLayout();
  G4double sipmHx = 0.0;
  G4double sipmHy = 0.0;
  G4double sipmHz = 0.0;
  G4ThreeVector sipmPos;

  const auto sipmLocalPositions = GetSiPMLocalPositions();
  ComputeSiPMPlacementFor(fSiPMFace,
                          sipmLocalPositions.front(),
                          sipmHx,
                          sipmHy,
                          sipmHz,
                          sipmPos);

  auto sipm_box = new G4Box("SiPM_Box", sipmHx, sipmHy, sipmHz);
  const G4ThreeVector sipmFullSize(2. * sipm_box->GetXHalfLength(),
                                 2. * sipm_box->GetYHalfLength(),
                                 2. * sipm_box->GetZHalfLength());
  G4cout << "SiPM geometry: full_size=" << sipmFullSize / mm << " mm" << G4endl;

  fSiPM_LV = new G4LogicalVolume(sipm_box, fSiPMMaterial, "SiPM");

  // Visual attributes of SiPM
  auto sipmVis = new G4VisAttributes(G4Colour(0.0, 1.0, 0.0, 0.9));
  sipmVis->SetForceSolid(true);
  fSiPM_LV->SetVisAttributes(sipmVis);

  for (G4int layer = 0; layer < layerCount; ++layer) {
    for (std::size_t index = 0; index < sipmLocalPositions.size(); ++index) {
      G4double placementHx = 0.0;
      G4double placementHy = 0.0;
      G4double placementHz = 0.0;
      G4ThreeVector placementPos;
      ComputeSiPMPlacementFor(fSiPMFace,
                              sipmLocalPositions[index],
                              placementHx,
                              placementHy,
                              placementHz,
                              placementPos);
      if (fStackEnabled) {
        placementPos.setZ(placementPos.z() + GetStackTileCenterZ(layer));
      }
      const G4int copyNumber = fStackEnabled
        ? GetStackSensorStride() * layer + static_cast<G4int>(index)
        : static_cast<G4int>(index);

      auto placement = new G4PVPlacement(nullptr,
                                         placementPos,
                                         fSiPM_LV,
                                         "SiPM",
                                         fWorld_LV,
                                         false,
                                         copyNumber,
                                         true);  // overlap check
      fSiPMs.push_back(placement);
      if (layer == 0 && index == 0) {
        fSiPM = placement;
      }
      G4cout << "SiPM placement: layout=" << fSiPMLayout
             << ", layer=" << layer
             << ", local_sensor=" << index
             << ", copy=" << copyNumber
             << ", face=" << fSiPMFace
             << ", local=" << sipmLocalPositions[index] / mm
             << " mm, world=" << placementPos / mm << " mm" << G4endl;
    }
  }

  if (IsStackLayoutStudy()) {
    ValidateStackLayoutGeometry();
  }

  // ------------- Surface --------------

  G4LogicalBorderSurface* surface = nullptr;
  for (G4int layer = 0; layer < layerCount; ++layer) {
    const auto name = "TankToWorldSurface_" + std::to_string(layer);
    auto layerSurface =
      new G4LogicalBorderSurface(name, fTanks[layer], world_PV, fSurface);
    if (layer == 0) {
      surface = layerSurface;
    }
  }

  // The tile remains painted/wrapped where it touches the steel. Border
  // surfaces are ordered, so this explicitly covers photons leaving the tile
  // toward the absorber while the existing tile-to-world boundary remains.
  if (fAbsorberEnabled) {
    for (G4int layer = 0; layer < layerCount; ++layer) {
      new G4LogicalBorderSurface(
        "TankToUpstreamSteelSurface_" + std::to_string(layer),
        fTanks[layer],
        fAbsorbers[layer],
        fSurface);
      if (fStackEnabled && !IsStackLayoutStudy() && layer + 1 < layerCount) {
        new G4LogicalBorderSurface(
          "TankToDownstreamSteelSurface_" + std::to_string(layer),
          fTanks[layer],
          fAbsorbers[layer + 1],
          fSurface);
      }
    }
  }

  auto opticalSurface =
    dynamic_cast<G4OpticalSurface*>(surface->GetSurface(fTank, world_PV)->GetSurfaceProperty());
  G4cout << "******  opticalSurface->DumpInfo:" << G4endl;
  if (opticalSurface) {
    opticalSurface->DumpInfo();
  }
  G4cout << "******  end of opticalSurface->DumpInfo" << G4endl;

  if (fIssue9GeometryAudit) AuditIssue9Geometry();
  return world_PV;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetSurfaceSigmaAlpha(G4double v)
{
  fSurface->SetSigmaAlpha(v);
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Surface sigma alpha set to: " << fSurface->GetSigmaAlpha() << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetSurfacePolish(G4double v)
{
  fSurface->SetPolish(v);
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Surface polish set to: " << fSurface->GetPolish() << G4endl;
}

void DetectorConstruction::ResetSurfaceMaterialPropertiesTable()
{
  delete fSurfaceMPT;
  fSurfaceMPT = new G4MaterialPropertiesTable();
  fSurface->SetMaterialPropertiesTable(fSurfaceMPT);
}

void DetectorConstruction::SetSurfacePreset(const G4String& preset)
{
  ResetSurfaceMaterialPropertiesTable();
  fSurface->SetModel(unified);
  fSurface->SetType(dielectric_dielectric);

  const auto addConstantReflectivity = [&]() {
    const G4int nEntries = 2;
    G4double photonEnergy[nEntries] = {2.0 * eV, 3.3 * eV};
    G4double reflectivity[nEntries] = {0.95, 0.95};
    fSurfaceMPT->AddProperty("REFLECTIVITY", photonEnergy, reflectivity, nEntries);
  };

  if (preset == "polished") {
    fSurface->SetFinish(polished);
    fSurface->SetSigmaAlpha(0.0);
  }
  else if (preset == "ground") {
    fSurface->SetFinish(ground);
    fSurface->SetSigmaAlpha(0.2);
  }
  else if (preset == "wrapped") {
    fSurface->SetFinish(polishedfrontpainted);
    fSurface->SetSigmaAlpha(0.0);
    addConstantReflectivity();
  }
  else if (preset == "polishedfrontpainted") {
    fSurface->SetFinish(polishedfrontpainted);
    fSurface->SetSigmaAlpha(0.0);
  }
  else if (preset == "groundfrontpainted") {
    fSurface->SetFinish(groundfrontpainted);
    fSurface->SetSigmaAlpha(0.2);
  }
  else if (preset == "polishedbackpainted") {
    fSurface->SetFinish(polishedbackpainted);
    fSurface->SetSigmaAlpha(0.0);
  }
  else if (preset == "groundbackpainted") {
    fSurface->SetFinish(groundbackpainted);
    fSurface->SetSigmaAlpha(0.2);
  }
  else {
    G4ExceptionDescription msg;
    msg << "Invalid surface preset: " << preset
        << ". Use polished, ground, wrapped, polishedfrontpainted, "
        << "groundfrontpainted, polishedbackpainted, or groundbackpainted.";
    G4Exception("DetectorConstruction::SetSurfacePreset",
                "OpNovice2_Surface_001",
                FatalException,
                msg);
  }

  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Surface preset applied: " << preset
         << " (model=unified, type=dielectric_dielectric, finish="
         << fSurface->GetFinish() << ", sigma_alpha="
         << fSurface->GetSigmaAlpha() << ")" << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddTankMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  fTankMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the box is now: " << G4endl;
  fTankMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddWorldMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  fWorldMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the world is now: " << G4endl;
  fWorldMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddSurfaceMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  if (fSurfaceMPT->GetProperty(prop) != nullptr) {
    fSurfaceMPT->RemoveProperty(prop);
  }
  fSurfaceMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the surface is now: " << G4endl;
  fSurfaceMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddTankMPC(const G4String& prop, G4double v)
{
  fTankMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the box is now: " << G4endl;
  fTankMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddWorldMPC(const G4String& prop, G4double v)
{
  fWorldMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the world is now: " << G4endl;
  fWorldMPT->DumpTable();
  G4cout << "............." << G4endl;
}
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddSurfaceMPC(const G4String& prop, G4double v)
{
  if (fSurfaceMPT->ConstPropertyExists(prop)) {
    fSurfaceMPT->RemoveConstProperty(prop);
  }
  fSurfaceMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the surface is now: " << G4endl;
  fSurfaceMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddGreaseMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  if (fGreaseMPT->GetProperty(prop) != nullptr) {
    fGreaseMPT->RemoveProperty(prop);
  }
  fGreaseMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the grease is now: " << G4endl;
  fGreaseMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddGreaseMPC(const G4String& prop, G4double v)
{
  if (fGreaseMPT->ConstPropertyExists(prop)) {
    fGreaseMPT->RemoveConstProperty(prop);
  }
  fGreaseMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the grease is now: " << G4endl;
  fGreaseMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetWorldMaterial(const G4String& mat)
{
  G4Material* pmat = G4NistManager::Instance()->FindOrBuildMaterial(mat);
  if (pmat && fWorldMaterial != pmat) {
    fWorldMaterial = pmat;
    if (fWorld_LV) {
      fWorld_LV->SetMaterial(fWorldMaterial);
      fWorldMaterial->SetMaterialPropertiesTable(fWorldMPT);
    }
    G4RunManager::GetRunManager()->PhysicsHasBeenModified();
    G4cout << "World material set to " << fWorldMaterial->GetName() << G4endl;
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetTankMaterial(const G4String& mat)
{
  G4Material* pmat = G4NistManager::Instance()->FindOrBuildMaterial(mat);
  if (pmat && fTankMaterial != pmat) {
    fTankMaterial = pmat;
    if (fTank_LV) {
      fTank_LV->SetMaterial(fTankMaterial);
      fTankMaterial->SetMaterialPropertiesTable(fTankMPT);
      fTankMaterial->GetIonisation()->SetBirksConstant(0.126 * mm / MeV);
    }
    G4RunManager::GetRunManager()->PhysicsHasBeenModified();
    G4cout << "Tank material set to " << fTankMaterial->GetName() << G4endl;
  }
}

void DetectorConstruction::SetTankSize(const G4ThreeVector& fullSize)
{
  if (fullSize.x() <= 0. || fullSize.y() <= 0. || fullSize.z() <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid tank full size: " << fullSize / cm
        << " cm. All dimensions must be positive.";
    G4Exception("DetectorConstruction::SetTankSize",
                "OpNovice2_Tank_001",
                FatalException,
                msg);
  }

  fTank_x = 0.5 * fullSize.x();
  fTank_y = 0.5 * fullSize.y();
  fTank_z = 0.5 * fullSize.z();
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Tank full size set to "
         << 2. * fTank_x / cm << " x "
         << 2. * fTank_y / cm << " x "
         << 2. * fTank_z / cm << " cm" << G4endl;
}

void DetectorConstruction::SetTankSizePreset(const G4String& preset)
{
  if (preset == "5x5x0p4") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 0.4 * cm));
  }
  else if (preset == "5x5x0p8") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 0.8 * cm));
  }
  else if (preset == "5x5x1p6") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 1.6 * cm));
  }
  else if (preset == "10x10x0p4") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 0.4 * cm));
  }
  else if (preset == "10x10x0p8") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 0.8 * cm));
  }
  else if (preset == "10x10x1p6") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 1.6 * cm));
  }
  else {
    G4ExceptionDescription msg;
    msg << "Unknown tank size preset: " << preset
        << ". Use 5x5x0p4, 5x5x0p8, 5x5x1p6, "
        << "10x10x0p4, 10x10x0p8, or 10x10x1p6.";
    G4Exception("DetectorConstruction::SetTankSizePreset",
                "OpNovice2_Tank_002",
                FatalException,
                msg);
  }
}

void DetectorConstruction::SetAbsorberEnabled(G4bool enabled)
{
  fAbsorberEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Steel absorber enabled set to "
         << (fAbsorberEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetAbsorberSize(const G4ThreeVector& fullSize)
{
  if (fullSize.x() <= 0. || fullSize.y() <= 0. || fullSize.z() <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid absorber full size: " << fullSize / mm
        << " mm. All dimensions must be positive.";
    G4Exception("DetectorConstruction::SetAbsorberSize",
                "OpNovice2_Absorber_001",
                FatalException,
                msg);
  }

  fAbsorber_x = 0.5 * fullSize.x();
  fAbsorber_y = 0.5 * fullSize.y();
  fAbsorber_z = 0.5 * fullSize.z();
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Steel absorber full size set to "
         << 2. * fAbsorber_x / mm << " x "
         << 2. * fAbsorber_y / mm << " x "
         << 2. * fAbsorber_z / mm << " mm" << G4endl;
}

void DetectorConstruction::SetStackEnabled(G4bool enabled)
{
  fStackEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Longitudinal stack enabled set to "
         << (fStackEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetStackLayoutStudy(G4bool enabled)
{
  fStackLayoutStudy = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Stack layout study set to " << (enabled ? "true" : "false") << G4endl;
}

G4int DetectorConstruction::GetStackSensorsPerLayer() const
{
  if (!IsStackLayoutStudy()) return 2;
  if (fSiPMLayout == "back-four") return 4;
  if (fSiPMLayout == "back-two" || fSiPMLayout == "edge-two") return 2;
  if (fSiPMLayout == "back-center") return 1;
  return 0;
}

void DetectorConstruction::SetStackLayers(G4int layers)
{
  if (layers <= 0) {
    G4ExceptionDescription msg;
    msg << "Invalid stack layer count: " << layers << ". Use a positive integer.";
    G4Exception("DetectorConstruction::SetStackLayers",
                "OpNovice2_Stack_001",
                FatalException,
                msg);
  }
  fStackLayers = layers;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Longitudinal stack layer count set to " << fStackLayers << G4endl;
}

G4double DetectorConstruction::GetStackSteelCenterZ(G4int layer) const
{
  const G4double moduleLength = 2. * fAbsorber_z + 2. * fTank_z + GetStackReadoutGap();
  return 0.5 * GetStackLength() - layer * moduleLength - fAbsorber_z;
}

G4double DetectorConstruction::GetStackTileCenterZ(G4int layer) const
{
  const G4double moduleLength = 2. * fAbsorber_z + 2. * fTank_z + GetStackReadoutGap();
  return 0.5 * GetStackLength() - layer * moduleLength
         - 2. * fAbsorber_z - fTank_z;
}

G4int DetectorConstruction::GetTileLayer(const G4VPhysicalVolume* volume) const
{
  const auto found = std::find(fTanks.begin(), fTanks.end(), volume);
  return found == fTanks.end()
    ? -1
    : static_cast<G4int>(std::distance(fTanks.begin(), found));
}

G4int DetectorConstruction::GetAbsorberLayer(const G4VPhysicalVolume* volume) const
{
  const auto found = std::find(fAbsorbers.begin(), fAbsorbers.end(), volume);
  return found == fAbsorbers.end()
    ? -1
    : static_cast<G4int>(std::distance(fAbsorbers.begin(), found));
}

G4int DetectorConstruction::GetSensorLayer(G4int copyNumber) const
{
  if (!fStackEnabled) {
    return copyNumber >= 0 ? 0 : -1;
  }
  const auto stride = GetStackSensorStride();
  return copyNumber >= 0 && copyNumber < stride * fStackLayers
         && copyNumber % stride < GetStackSensorsPerLayer()
    ? copyNumber / stride
    : -1;
}

G4int DetectorConstruction::GetSensorLocalIndex(G4int copyNumber) const
{
  if (!fStackEnabled) {
    return copyNumber;
  }
  const auto stride = GetStackSensorStride();
  return copyNumber >= 0 && copyNumber < stride * fStackLayers
         && copyNumber % stride < GetStackSensorsPerLayer()
    ? copyNumber % stride
    : -1;
}

void DetectorConstruction::SetBottomCavityEnabled(G4bool enabled)
{
  fBottomCavityEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Tank bottom cavity set to "
         << (fBottomCavityEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetDimpleEnabled(G4bool enabled)
{
  fDimpleEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple set to "
         << (fDimpleEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetDimpleFrontCompensation(G4bool enabled)
{
  fDimpleFrontCompensation = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Dimple front compensation set to "
         << (enabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetDimpleRadius(G4double radius)
{
  if (radius <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid dimple radius: " << radius / mm
        << " mm. Radius must be positive.";
    G4Exception("DetectorConstruction::SetDimpleRadius",
                "OpNovice2_Dimple_012",
                FatalException,
                msg);
  }

  fDimpleRadius = radius;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple radius set to " << fDimpleRadius / mm << " mm" << G4endl;
}

void DetectorConstruction::SetDimpleMode(const G4String& mode)
{
  if (mode != "hemisphere" && mode != "cube") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple mode: " << mode
        << ". Use hemisphere or cube.";
    G4Exception("DetectorConstruction::SetDimpleMode",
                "OpNovice2_Dimple_013",
                FatalException,
                msg);
  }

  fDimpleMode = mode;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple mode set to " << fDimpleMode << G4endl;
}

void DetectorConstruction::SetDimpleSiPMMode(const G4String& mode)
{
  if (mode != "surface" && mode != "opening" && mode != "external") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple SiPM mode: " << mode
        << ". Use surface, opening, or external.";
    G4Exception("DetectorConstruction::SetDimpleSiPMMode",
                "OpNovice2_Dimple_014",
                FatalException,
                msg);
  }

  fDimpleSiPMMode = mode;
  fDimpleSiPMModeExplicit = true;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple SiPM mode set to " << fDimpleSiPMMode << G4endl;
}

G4double DetectorConstruction::GetBottomCavityRadius() const
{
  const G4double tankThickness = 2. * fTank_z;
  const G4double minimumTopThickness = 0.25 * tankThickness;
  return tankThickness - minimumTopThickness;
}

G4String DetectorConstruction::GetEffectiveDimpleSiPMMode() const
{
  if (fDimpleSiPMModeExplicit && fSiPMCavityModeExplicit &&
      fDimpleSiPMMode != fSiPMCavityMode) {
    G4ExceptionDescription msg;
    msg << "Conflicting dimple SiPM placement modes: /opnovice2/dimple/sipmMode="
        << fDimpleSiPMMode << " but legacy /opnovice2/sipm/cavityMode="
        << fSiPMCavityMode << ". Use one mode, or set both to the same value.";
    G4Exception("DetectorConstruction::GetEffectiveDimpleSiPMMode",
                "OpNovice2_Dimple_002",
                FatalException,
                msg);
  }

  if (!fDimpleSiPMModeExplicit && fSiPMCavityModeExplicit) {
    return fSiPMCavityMode;
  }

  return fDimpleSiPMMode;
}

G4double DetectorConstruction::GetSiPMFootprintCornerRadius(G4double u,
                                                            G4double v,
                                                            G4double hu,
                                                            G4double hv) const
{
  const G4double cornerRadii[] = {
    std::sqrt((u - hu) * (u - hu) + (v - hv) * (v - hv)),
    std::sqrt((u - hu) * (u - hu) + (v + hv) * (v + hv)),
    std::sqrt((u + hu) * (u + hu) + (v - hv) * (v - hv)),
    std::sqrt((u + hu) * (u + hu) + (v + hv) * (v + hv))
  };

  G4double rMax = cornerRadii[0];
  for (G4int i = 1; i < 4; ++i) {
    rMax = std::max(rMax, cornerRadii[i]);
  }
  return rMax;
}

void DetectorConstruction::ValidateAbsorberConfiguration() const
{
  if (fAbsorber_x < fTank_x || fAbsorber_y < fTank_y) {
    G4ExceptionDescription msg;
    msg << "The steel absorber must cover the tile transversely. "
        << "absorber full size=" << 2. * fAbsorber_x / mm << " x "
        << 2. * fAbsorber_y / mm << " mm, tile full size="
        << 2. * fTank_x / mm << " x " << 2. * fTank_y / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateAbsorberConfiguration",
                "OpNovice2_Absorber_002",
                FatalException,
                msg);
  }

  if (fAbsorber_x >= fExpHall_x || fAbsorber_y >= fExpHall_y ||
      GetAbsorberUpstreamFaceZ() >= fExpHall_z) {
    G4ExceptionDescription msg;
    msg << "The steel absorber does not fit strictly inside the world. "
        << "absorber half transverse size=" << fAbsorber_x / mm << " x "
        << fAbsorber_y / mm << " mm, upstream face z="
        << GetAbsorberUpstreamFaceZ() / mm << " mm; world half size="
        << fExpHall_x / mm << " x " << fExpHall_y / mm << " x "
        << fExpHall_z / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateAbsorberConfiguration",
                "OpNovice2_Absorber_003",
                FatalException,
                msg);
  }

  if (fSiPMFace == "+Z" || fSiPMFace == "top") {
    G4ExceptionDescription msg;
    msg << "A +Z SiPM would overlap the zero-gap steel absorber. "
        << "Attach the SiPM to another tile face; the neutron study presets "
        << "use -Z or a lateral face.";
    G4Exception("DetectorConstruction::ValidateAbsorberConfiguration",
                "OpNovice2_Absorber_004",
                FatalException,
                msg);
  }
}

void DetectorConstruction::ValidateStackConfiguration() const
{
  const G4double safety = 1.e-6 * mm;
  if (fStackLayers != 10) {
    G4ExceptionDescription msg;
    msg << "steel-module-stack-v1 requires exactly 10 layers; current value is "
        << fStackLayers << ".";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_002",
                FatalException,
                msg);
  }
  if (!fAbsorberEnabled) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack requires the SAE-304 absorber.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_003",
                FatalException,
                msg);
  }
  if (fBottomCavityEnabled || fDimpleEnabled || fGreaseEnabled) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack supports only the undimpled zero-gap "
        << "coupling proxy; cavity, dimple, and explicit grease must be disabled.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_004",
                FatalException,
                msg);
  }
  if (IsStackLayoutStudy()) {
    const auto equalLength = [](G4double value, G4double expected) {
      return std::isfinite(value) && std::abs(value - expected) <= 1.e-9 * mm;
    };
    const auto require = [](G4bool accepted, const char* message) {
      if (!accepted) {
        G4Exception("DetectorConstruction::ValidateStackConfiguration",
                    "OpNovice2_LayoutStudy_002", FatalException, message);
      }
    };
    require(equalLength(2.*fAbsorber_x, 500.*mm) &&
            equalLength(2.*fAbsorber_y, 500.*mm) &&
            equalLength(2.*fAbsorber_z, 40.*mm) &&
            equalLength(2.*fTank_x, 100.*mm) && equalLength(2.*fTank_y, 100.*mm),
            "Layout study requires 500x500x40 mm steel and whole 100x100 mm tiles.");
    G4bool thicknessAccepted = false;
    for (const auto thickness : {4., 8., 12., 16., 20., 24.}) {
      thicknessAccepted = thicknessAccepted || equalLength(2.*fTank_z, thickness*mm);
    }
    require(thicknessAccepted, "Layout study tile thickness must be 4, 8, 12, 16, 20, or 24 mm.");
    require(equalLength(fSiPMActiveU, 2.4*mm) && equalLength(fSiPMActiveV, 2.4*mm) &&
            equalLength(fSiPMThickness, 0.5*mm),
            "Layout study requires 2.4x2.4x0.5 mm SiPMs.");
    require(equalLength(fSiPMLocalPosition.x(), 0.) &&
            equalLength(fSiPMLocalPosition.y(), 0.) && equalLength(fSiPMLocalPosition.z(), 0.),
            "Layout study uses fixed sensor centres; set sipm/localPosition to 0 0 0 mm.");
    const G4bool side = fSiPMLayout == "edge-two" &&
                       (fSiPMFace == "+X" || fSiPMFace == "right");
    const G4bool back = (fSiPMLayout == "back-four" || fSiPMLayout == "back-two" ||
                        fSiPMLayout == "back-center") &&
                       (fSiPMFace == "-Z" || fSiPMFace == "bottom");
    require(side || back, "Layout study requires edge-two on +X or back-four/back-two/back-center on -Z.");
    require(0.5*GetStackLength() + 1.5*mm < fExpHall_z - safety,
            "Layout study source at core_length/2 + 1.5 mm must fit inside World.");
  }
  else if (fSiPMLayout != "edge-two" ||
      (fSiPMFace != "+X" && fSiPMFace != "right")) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack requires edge-two on the +X face.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_005",
                FatalException,
                msg);
  }
  if (0.5 * GetStackLength() >= fExpHall_z - safety ||
      fTank_x + fSiPMThickness >= fExpHall_x - safety ||
      fTank_y >= fExpHall_y - safety) {
    G4ExceptionDescription msg;
    msg << "The longitudinal stack does not fit strictly inside the world. "
        << "stack length=" << GetStackLength() / mm
        << " mm, tile/SiPM x extent=" << (fTank_x + fSiPMThickness) / mm
        << " mm, world half sizes=" << fExpHall_x / mm << " x "
        << fExpHall_y / mm << " x " << fExpHall_z / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateStackConfiguration",
                "OpNovice2_Stack_006",
                FatalException,
                msg);
  }
}

void DetectorConstruction::ValidateStackLayoutGeometry() const
{
  // All study volumes are unrotated boxes in World. Test their actual placed
  // bounds deterministically, including thin SiPMs and exact touching faces.
  // This adds no random draws to the constructor's existing overlap checks.
  const G4double tolerance = 1.e-9 * mm;
  std::vector<G4VPhysicalVolume*> volumes = fAbsorbers;
  volumes.insert(volumes.end(), fTanks.begin(), fTanks.end());
  volumes.insert(volumes.end(), fSiPMs.begin(), fSiPMs.end());
  const auto fail = [](const G4String& message) {
    G4Exception("DetectorConstruction::ValidateStackLayoutGeometry",
                "OpNovice2_LayoutStudy_003", FatalException, message.c_str());
  };
  if (fAbsorbers.size() != 10 || fTanks.size() != 10 ||
      fSiPMs.size() != static_cast<std::size_t>(10 * GetStackSensorsPerLayer())) {
    fail("Constructed layout study has the wrong steel, tile, or active sensor count.");
    return;
  }
  std::vector<G4ThreeVector> centers;
  std::vector<G4ThreeVector> halves;
  const G4ThreeVector worldHalf(fExpHall_x, fExpHall_y, fExpHall_z);
  for (const auto* volume : volumes) {
    const auto* box = dynamic_cast<const G4Box*>(volume->GetLogicalVolume()->GetSolid());
    // The obsolete pointer accessor can return an identity matrix for an
    // unrotated placement; validate the transformation value itself.
    if (!box || !volume->GetObjectRotationValue().isIdentity() ||
        volume->GetMotherLogical() != fWorld_LV) {
      fail("Layout study expects unrotated boxes placed directly in World.");
      return;
    }
    const auto center = volume->GetObjectTranslation();
    const G4ThreeVector half(box->GetXHalfLength(), box->GetYHalfLength(), box->GetZHalfLength());
    for (G4int axis = 0; axis < 3; ++axis) {
      if (!std::isfinite(center[axis]) || !std::isfinite(half[axis]) || half[axis] <= 0. ||
          std::abs(center[axis]) + half[axis] >= worldHalf[axis] - tolerance) {
        fail("Non-finite, invalid, or outside-World box in layout study: " + volume->GetName());
        return;
      }
    }
    centers.push_back(center);
    halves.push_back(half);
  }
  for (std::size_t a = 0; a < volumes.size(); ++a) {
    for (std::size_t b = a + 1; b < volumes.size(); ++b) {
      G4bool penetrates = true;
      for (G4int axis = 0; axis < 3; ++axis) {
        const auto overlap = halves[a][axis] + halves[b][axis]
                             - std::abs(centers[a][axis] - centers[b][axis]);
        penetrates = penetrates && overlap > tolerance;
      }
      if (penetrates) {
        fail("Overlapping layout-study boxes: " + volumes[a]->GetName() + "[" +
             std::to_string(volumes[a]->GetCopyNo()) + "] and " + volumes[b]->GetName() +
             "[" + std::to_string(volumes[b]->GetCopyNo()) + "].");
        return;
      }
    }
  }
  for (const auto* sensor : fSiPMs) {
    if (GetSensorLayer(sensor->GetCopyNo()) < 0 || GetSensorLocalIndex(sensor->GetCopyNo()) < 0) {
      fail("Inactive sensor copy was constructed in layout study.");
      return;
    }
  }
  G4cout << "Stack layout geometry: PASS, " << volumes.size()
         << " placed boxes, no positive-volume overlap, all contained in World; "
         << "9 internal gaps of 0.5 mm, final tile followed by World." << G4endl;
}

G4double DetectorConstruction::GetDimpleCubeSide() const
{
  // r remains the reference hemisphere radius, so changing the shape does
  // not change the analytic removed volume. Avoid any Monte Carlo volume API.
  return std::cbrt(2. * CLHEP::pi / 3.) * fDimpleRadius;
}

void DetectorConstruction::ValidateIssue9GeometryConfiguration() const
{
  const G4double safety = 1.e-6 * mm;
  if (fGreaseEnabled && fDimpleEnabled && GetEffectiveDimpleSiPMMode() == "external") {
    G4Exception("DetectorConstruction::ValidateIssue9GeometryConfiguration",
                "OpNovice2_Dimple_015", FatalException,
                "External dimple SiPM placement does not yet support grease coupling.");
  }
  if (fStackEnabled || fAbsorberEnabled || fBottomCavityEnabled || fGreaseEnabled ||
      fSiPMLayout != "single" || (fSiPMFace != "-Z" && fSiPMFace != "bottom") ||
      (fDimpleEnabled && GetEffectiveDimpleSiPMMode() != "external") ||
      !std::isfinite(fSiPMLocalPosition.x()) ||
      !std::isfinite(fSiPMLocalPosition.y()) ||
      !std::isfinite(fSiPMLocalPosition.z()) ||
      std::abs(fSiPMLocalPosition.x()) > safety ||
      std::abs(fSiPMLocalPosition.y()) > safety ||
      std::abs(fSiPMLocalPosition.z()) > safety) {
    G4Exception("DetectorConstruction::ValidateIssue9GeometryConfiguration",
                "OpNovice2_Issue9_001", FatalException,
                "Cube, front compensation, and issue9Geometry require one centered -Z SiPM, "
                "external placement for a dimple, and no stack, absorber, legacy cavity, or grease.");
  }
  for (const auto dimension : {fTank_x, fTank_y, fTank_z, fSiPMActiveU,
                               fSiPMActiveV, fSiPMThickness, fDimpleRadius,
                               fExpHall_x, fExpHall_y, fExpHall_z}) {
    if (!std::isfinite(dimension) || dimension <= safety) {
      G4Exception("DetectorConstruction::ValidateIssue9GeometryConfiguration",
                  "OpNovice2_Issue9_002", FatalException,
                  "Issue #9 geometry dimensions must be finite and positive with a safety margin.");
    }
  }
  const G4double depth = fDimpleEnabled
    ? (fDimpleMode == "cube" ? GetDimpleCubeSide() : fDimpleRadius) : 0.;
  if (depth >= 2. * fTank_z - safety) {
    G4Exception("DetectorConstruction::ValidateIssue9GeometryConfiguration",
                "OpNovice2_Issue9_003", FatalException,
                "The complete cavity depth must be smaller than the nominal tile thickness.");
  }
  const G4double footprint = fDimpleEnabled
    ? (fDimpleMode == "cube" ? 0.5 * GetDimpleCubeSide() : fDimpleRadius) : 0.;
  if (footprint >= fTank_x - safety || footprint >= fTank_y - safety ||
      0.5 * fSiPMActiveU > fTank_x + safety ||
      0.5 * fSiPMActiveV > fTank_y + safety) {
    G4Exception("DetectorConstruction::ValidateIssue9GeometryConfiguration",
                "OpNovice2_Issue9_004", FatalException,
                "The cavity footprint must be inside the tile and the SiPM must fit on its back face.");
  }
  const G4double frontExtent = fDimpleFrontCompensation ? depth : 0.;
  if (fTank_x >= fExpHall_x - safety || fTank_y >= fExpHall_y - safety ||
      fTank_z + frontExtent >= fExpHall_z - safety ||
      fTank_z + fSiPMThickness >= fExpHall_z - safety) {
    G4Exception("DetectorConstruction::ValidateIssue9GeometryConfiguration",
                "OpNovice2_Issue9_005", FatalException,
                "The tile, front compensation, and external SiPM must be fully inside the World.");
  }
}

void DetectorConstruction::AuditIssue9Geometry() const
{
  ValidateIssue9GeometryConfiguration();
  const auto require = [](G4bool condition, const char* message) {
    if (!condition) {
      G4Exception("DetectorConstruction::AuditIssue9Geometry", "OpNovice2_Issue9_006",
                  FatalException, message);
    }
  };
  require(fTanks.size() == 1 && fSiPMs.size() == 1 && fTank && fSiPM,
          "The constructed geometry must contain exactly one tile and one SiPM.");
  require(fTank->GetObjectTranslation() == G4ThreeVector() &&
          fTank->GetObjectRotationValue().isIdentity() &&
          fSiPM->GetObjectRotationValue().isIdentity(),
          "Issue #9 audit requires an unrotated centered tile and unrotated SiPM.");
  const auto* solid = fTank->GetLogicalVolume()->GetSolid();
  const auto* sensor = dynamic_cast<const G4Box*>(fSiPM->GetLogicalVolume()->GetSolid());
  require(solid && sensor, "Constructed tile solid and box SiPM are required.");
  require(fTank->GetLogicalVolume()->GetNoDaughters() == 0 &&
          fTank->GetLogicalVolume()->GetMaterial() == fTankMaterial,
          "The restored material must belong to the same seamless scintillator solid.");
  const G4double tolerance = 1.e-6 * mm;
  const G4double receivingPlane = fSiPM->GetObjectTranslation().z() + sensor->GetZHalfLength();
  require(std::abs(receivingPlane + fTank_z) < tolerance &&
          std::abs(fSiPM->GetObjectTranslation().x()) < tolerance &&
          std::abs(fSiPM->GetObjectTranslation().y()) < tolerance &&
          std::abs(2. * sensor->GetXHalfLength() - fSiPMActiveU) < tolerance &&
          std::abs(2. * sensor->GetYHalfLength() - fSiPMActiveV) < tolerance &&
          std::abs(2. * sensor->GetZHalfLength() - fSiPMThickness) < tolerance,
          "The actual external sensor position or dimensions differ from the requested geometry.");

  const G4double side = GetDimpleCubeSide();
  const G4double volume = fDimpleEnabled
    ? 2. * CLHEP::pi * fDimpleRadius * fDimpleRadius * fDimpleRadius / 3. : 0.;
  const G4double frontExtent = fDimpleFrontCompensation
    ? (fDimpleMode == "cube" ? side : fDimpleRadius) : 0.;
  const G4String shape = fDimpleEnabled ? fDimpleMode : "flat";
  const auto oldPrecision = G4cout.precision();
  G4cout << std::setprecision(17)
         << "Issue9 geometry: shape=" << shape
         << " front_compensation=" << (fDimpleFrontCompensation ? 1 : 0)
         << " reference_radius_mm=" << fDimpleRadius / mm
         << " cube_side_mm=" << side / mm
         << " removed_volume_mm3=" << volume / (mm * mm * mm)
         << " restored_volume_mm3=" << (fDimpleFrontCompensation ? volume : 0.) / (mm * mm * mm)
         << " nominal_volume_mm3=" << 8. * fTank_x * fTank_y * fTank_z / (mm * mm * mm)
         << " analytic_scintillator_volume_mm3="
         << (8. * fTank_x * fTank_y * fTank_z - volume
             + (fDimpleFrontCompensation ? volume : 0.)) / (mm * mm * mm)
         << " front_extent_mm=" << frontExtent / mm
         << " receiving_plane_z_mm=" << receivingPlane / mm
         << " solid_type=" << solid->GetEntityType() << G4endl;

  // Probe the actual Boolean solid. No GetCubicVolume/overlap sampler is called
  // here: those may consume RNG state, whereas these navigation queries do not.
  struct RayProbe { const char* label; G4double x; G4double y; };
  const std::vector<RayProbe> probes = {
    {"center", 0., 0.},
    {"sphere_inner", 0.9 * fDimpleRadius, 0.},
    {"sphere_outer", 1.1 * fDimpleRadius, 0.},
    {"cube_inner", 0.45 * side, 0.},
    {"cube_outer", 0.55 * side, 0.},
    {"diagonal_inner", 0.45 * fDimpleRadius, 0.45 * fDimpleRadius},
    {"diagonal_outer", 0.8 * fDimpleRadius, 0.8 * fDimpleRadius},
    {"far_x", 0.8 * fTank_x, 0.},
    {"far_y", 0., 0.8 * fTank_y}
  };
  G4int insideProbes = 0;
  for (const auto& ray : probes) {
    const G4double x = std::min(ray.x, 0.95 * fTank_x);
    const G4double y = std::min(ray.y, 0.95 * fTank_y);
    G4double height = 0.;
    if (fDimpleEnabled) {
      if (fDimpleMode == "cube") {
        if (std::abs(x) < 0.5 * side && std::abs(y) < 0.5 * side) height = side;
      }
      else if (x * x + y * y < fDimpleRadius * fDimpleRadius) {
        height = std::sqrt(fDimpleRadius * fDimpleRadius - x * x - y * y);
      }
    }
    const G4double expectedEntry = fTank_z + (fDimpleFrontCompensation ? height : 0.);
    const G4double expectedExit = -fTank_z + height;
    const G4double expectedThickness = expectedEntry - expectedExit;
    const G4double step = std::min(1.e-4 * mm, 0.1 * expectedThickness);
    const G4ThreeVector direction(0., 0., -1.);
    const G4ThreeVector start(x, y, fTank_z + frontExtent + mm);
    require(solid->Inside(start) == kOutside, "A vertical-ray starting point is not outside the tile.");
    const G4double distanceIn = solid->DistanceToIn(start, direction);
    require(std::isfinite(distanceIn) && distanceIn < 2. * fExpHall_z,
            "The actual solid did not admit an expected vertical ray.");
    const G4double entry = start.z() - distanceIn;
    const G4ThreeVector inside(x, y, entry - step);
    require(solid->Inside(inside) == kInside, "The vertical ray did not enter solid material.");
    const G4double distanceOut = solid->DistanceToOut(inside, direction);
    require(std::isfinite(distanceOut) && distanceOut < 2. * fExpHall_z,
            "The actual solid did not provide a finite vertical exit.");
    const G4double exit = inside.z() - distanceOut;
    const G4double thickness = entry - exit;
    G4cout << "Issue9 ray: label=" << ray.label << " x_mm=" << x / mm << " y_mm=" << y / mm
           << " entry_z_mm=" << entry / mm << " exit_z_mm=" << exit / mm
           << " thickness_mm=" << thickness / mm
           << " expected_entry_z_mm=" << expectedEntry / mm
           << " expected_exit_z_mm=" << expectedExit / mm
           << " expected_thickness_mm=" << expectedThickness / mm << G4endl;
    require(std::abs(entry - expectedEntry) < tolerance &&
            std::abs(exit - expectedExit) < tolerance &&
            std::abs(thickness - expectedThickness) < tolerance,
            "Actual vertical-ray intersections disagree with the intended material profile.");
    const G4ThreeVector afterExit(x, y, exit - step);
    require(solid->Inside(afterExit) == kOutside &&
            solid->DistanceToIn(afterExit, direction) > 2. * fExpHall_z,
            "The vertical ray found an unexpected extra material segment.");
    const G4double sampleZ[] = {expectedEntry + step, expectedEntry - step,
                                0.5 * (expectedEntry + expectedExit),
                                expectedExit + step, expectedExit - step};
    const EInside expectedStates[] = {kOutside, kInside, kInside, kInside, kOutside};
    for (G4int index = 0; index < 5; ++index) {
      const auto observed = solid->Inside(G4ThreeVector(x, y, sampleZ[index]));
      G4cout << "Issue9 inside: label=" << ray.label << "_" << index
             << " x_mm=" << x / mm << " y_mm=" << y / mm << " z_mm=" << sampleZ[index] / mm
             << " observed=" << static_cast<G4int>(observed)
             << " expected=" << static_cast<G4int>(expectedStates[index]) << G4endl;
      require(observed == expectedStates[index], "An actual-solid Inside probe failed.");
      ++insideProbes;
    }
  }
  G4cout << "Issue9 geometry audit: PASS rays=" << probes.size()
         << " inside_probes=" << insideProbes << G4endl;
  G4cout.precision(oldPrecision);
}


void DetectorConstruction::ValidateDimpleConfiguration() const
{
  const G4double safety = 1.e-6 * mm;

  if (fDimpleMode != "hemisphere" && fDimpleMode != "cube") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple mode: " << fDimpleMode
        << ". Use hemisphere or cube.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_003",
                FatalException,
                msg);
  }

  if (!std::isfinite(fDimpleRadius) || fDimpleRadius <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid dimple radius: " << fDimpleRadius / mm
        << " mm. Radius must be finite and positive.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_004",
                FatalException,
                msg);
  }

  const G4double tankThickness = 2. * fTank_z;
  if (fDimpleRadius >= tankThickness - safety) {
    G4ExceptionDescription msg;
    msg << "Strict hemispherical dimple radius is too large for this tile. "
        << "radius=" << fDimpleRadius / mm
        << " mm, tile thickness=" << tankThickness / mm
        << " mm. Radius must be smaller than thickness with safety margin.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_005",
                FatalException,
                msg);
  }

  if (fSiPMFace != "-Z" && fSiPMFace != "bottom") {
    G4ExceptionDescription msg;
    msg << "Week 8.1 dimple supports only bottom-center -Z SiPM placement. "
        << "Current /opnovice2/sipm/face is " << fSiPMFace << ".";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_006",
                FatalException,
                msg);
  }

  if (!std::isfinite(fSiPMLocalPosition.x()) ||
      !std::isfinite(fSiPMLocalPosition.y()) ||
      !std::isfinite(fSiPMLocalPosition.z()) ||
      std::abs(fSiPMLocalPosition.x()) > safety ||
      std::abs(fSiPMLocalPosition.y()) > safety ||
      std::abs(fSiPMLocalPosition.z()) > safety) {
    G4ExceptionDescription msg;
    msg << "Week 8.1 dimple supports only bottom-center SiPM local position "
        << "0 0 0. Current local position is "
        << fSiPMLocalPosition / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_007",
                FatalException,
                msg);
  }

  const G4String sipmMode = GetEffectiveDimpleSiPMMode();
  if (sipmMode != "surface" && sipmMode != "opening" && sipmMode != "external") {
    G4ExceptionDescription msg;
    msg << "Unknown dimple SiPM mode: " << sipmMode
        << ". Use surface, opening, or external.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_009",
                FatalException,
                msg);
  }

  const G4double hu = 0.5 * fSiPMActiveU;
  const G4double hv = 0.5 * fSiPMActiveV;
  if (sipmMode == "external") {
    if (fGreaseEnabled) {
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_015", FatalException,
                  "External dimple SiPM placement does not yet support grease coupling.");
    }
    if (!std::isfinite(fSiPMActiveU) || !std::isfinite(fSiPMActiveV) ||
        !std::isfinite(fSiPMThickness) || fSiPMActiveU <= 0. ||
        fSiPMActiveV <= 0. || fSiPMThickness <= 0.) {
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_016", FatalException,
                  "External dimple SiPM dimensions must be finite and positive.");
    }
    if (!std::isfinite(fTank_x) || !std::isfinite(fTank_y) ||
        !std::isfinite(fTank_z) || fTank_x <= 0. || fTank_y <= 0. || fTank_z <= 0.) {
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_018", FatalException,
                  "External dimple placement requires finite, positive tile dimensions.");
    }
    if (std::abs(fSiPMLocalPosition.x()) + hu > fTank_x + safety ||
        std::abs(fSiPMLocalPosition.y()) + hv > fTank_y + safety) {
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_017", FatalException,
                  "External dimple SiPM footprint must fit on the tile bottom face.");
    }
    const auto* worldBox = fWorld_LV
      ? dynamic_cast<const G4Box*>(fWorld_LV->GetSolid()) : nullptr;
    if (!worldBox || !std::isfinite(worldBox->GetXHalfLength()) ||
        !std::isfinite(worldBox->GetYHalfLength()) ||
        !std::isfinite(worldBox->GetZHalfLength()) ||
        std::abs(fSiPMLocalPosition.x()) + hu >= worldBox->GetXHalfLength() - safety ||
        std::abs(fSiPMLocalPosition.y()) + hv >= worldBox->GetYHalfLength() - safety ||
        fTank_z + fSiPMThickness >= worldBox->GetZHalfLength() - safety) {
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_018", FatalException,
                  "External dimple SiPM must be fully contained inside the constructed World.");
    }
    // The sensor is entirely below the original bottom plane. Its rectangular
    // face may cover the circular opening; its corners need not fit in the void.
    return;
  }

  const G4double rMax = GetSiPMFootprintCornerRadius(0., 0., hu, hv);
  if (rMax >= fDimpleRadius - safety) {
    G4ExceptionDescription msg;
    msg << "SiPM active face does not fit inside the dimple opening. "
        << "farthest corner radius=" << rMax / mm
        << " mm, dimple radius=" << fDimpleRadius / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_008",
                FatalException,
                msg);
  }

  if (sipmMode == "opening") {
    const G4double dimpleTopAtFootprint =
      -fTank_z + std::sqrt(fDimpleRadius * fDimpleRadius - rMax * rMax);
    const G4double topFace = -fTank_z + fSiPMThickness;
    if (topFace >= dimpleTopAtFootprint - safety) {
      G4ExceptionDescription msg;
      msg << "Dimple opening placement would overlap EJ-200. "
          << "top face z=" << topFace / mm
          << " mm, dimple clearance z=" << dimpleTopAtFootprint / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_010",
                  FatalException,
                  msg);
    }
  }
}

G4double DetectorConstruction::GetGreaseActiveU() const
{
  return fGreaseActiveU > 0.0 ? fGreaseActiveU : fSiPMActiveU;
}

G4double DetectorConstruction::GetGreaseActiveV() const
{
  return fGreaseActiveV > 0.0 ? fGreaseActiveV : fSiPMActiveV;
}

void DetectorConstruction::ValidateGreaseConfiguration() const
{
  const G4double safety = 1.e-6 * mm;

  if (fSiPMFace != "-Z" && fSiPMFace != "bottom") {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease coupling currently supports only -Z SiPM placement. "
        << "Current /opnovice2/sipm/face is " << fSiPMFace << ".";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_001",
                FatalException,
                msg);
  }

  if (fBottomCavityEnabled) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease coupling cannot be combined with bottom cavity geometry.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_002",
                FatalException,
                msg);
  }

  if (std::abs(fSiPMLocalPosition.x()) > safety ||
      std::abs(fSiPMLocalPosition.y()) > safety ||
      std::abs(fSiPMLocalPosition.z()) > safety) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease coupling currently supports only bottom-center SiPM local "
        << "position 0 0 0. Current local position is "
        << fSiPMLocalPosition / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_003",
                FatalException,
                msg);
  }

  if (!fDimpleEnabled && fGreaseThickness <= 0.0) {
    G4ExceptionDescription msg;
    msg << "EJ-550 flat-pad thickness must be positive when grease coupling is enabled.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_004",
                FatalException,
                msg);
  }

  const G4double greaseU = GetGreaseActiveU();
  const G4double greaseV = GetGreaseActiveV();
  if (greaseU <= 0.0 || greaseV <= 0.0) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease active size must be positive. Current size is "
        << greaseU / mm << " x " << greaseV / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_005",
                FatalException,
                msg);
  }

  if (0.5 * greaseU > fTank_x + safety || 0.5 * greaseV > fTank_y + safety) {
    G4ExceptionDescription msg;
    msg << "EJ-550 grease pad does not fit on the tile bottom face. "
        << "grease=" << greaseU / mm << " x " << greaseV / mm
        << " mm, tile=" << (2.0 * fTank_x) / mm << " x "
        << (2.0 * fTank_y) / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                "OpNovice2_Grease_006",
                FatalException,
                msg);
  }

  if (fDimpleEnabled) {
    if (fGreaseThickness > safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap coupling derives its thickness from the curved "
          << "dimple-to-SiPM gap; do not set /opnovice2/grease/thickness.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_007",
                  FatalException,
                  msg);
    }

    if (greaseU > fSiPMActiveU + safety || greaseV > fSiPMActiveV + safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap footprint cannot exceed the SiPM active face. "
          << "grease=" << greaseU / mm << " x " << greaseV / mm
          << " mm, SiPM=" << fSiPMActiveU / mm << " x "
          << fSiPMActiveV / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_008",
                  FatalException,
                  msg);
    }

    const G4double greaseCornerRadius =
      std::sqrt(0.25 * greaseU * greaseU + 0.25 * greaseV * greaseV);
    if (greaseCornerRadius >= fDimpleRadius - safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap footprint does not fit inside the dimple. "
          << "corner radius=" << greaseCornerRadius / mm
          << " mm, dimple radius=" << fDimpleRadius / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_009",
                  FatalException,
                  msg);
    }

    G4double sipmHx = 0.0;
    G4double sipmHy = 0.0;
    G4double sipmHz = 0.0;
    G4ThreeVector sipmPos;
    ComputeSiPMPlacement(sipmHx, sipmHy, sipmHz, sipmPos);
    const G4double localBottom = sipmPos.z() + sipmHz + fTank_z;
    if (localBottom >= fDimpleRadius - safety) {
      G4ExceptionDescription msg;
      msg << "EJ-550 dimple-gap has no positive clearance above the SiPM. "
          << "SiPM top relative to dimple center=" << localBottom / mm
          << " mm, dimple radius=" << fDimpleRadius / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateGreaseConfiguration",
                  "OpNovice2_Grease_010",
                  FatalException,
                  msg);
    }
  }
}

void DetectorConstruction::ComputeGreasePlacement(G4double& hx,
                                                  G4double& hy,
                                                  G4double& hz,
                                                  G4ThreeVector& pos) const
{
  hx = 0.5 * GetGreaseActiveU();
  hy = 0.5 * GetGreaseActiveV();
  hz = 0.5 * fGreaseThickness;
  pos = G4ThreeVector(0.0, 0.0, -fTank_z - hz);
}


void DetectorConstruction::ComputeSiPMPlacement(G4double& hx,
                                                G4double& hy,
                                                G4double& hz,
                                                G4ThreeVector& pos) const
{
  ComputeSiPMPlacementFor(fSiPMFace, fSiPMLocalPosition, hx, hy, hz, pos);
}

void DetectorConstruction::ComputeSiPMPlacementFor(
  const G4String& face,
  const G4ThreeVector& localPosition,
  G4double& hx,
  G4double& hy,
  G4double& hz,
  G4ThreeVector& pos) const
{
  const G4double hu = 0.5 * fSiPMActiveU;
  const G4double hv = 0.5 * fSiPMActiveV;
  const G4double ht = 0.5 * fSiPMThickness;

  const G4double u = localPosition.x();
  const G4double v = localPosition.y();

  if (face == "+X" || face == "right") {
    hx = ht;
    hy = hu;
    hz = hv;
    pos = G4ThreeVector(fTank_x + ht, u, v);
  }
  else if (face == "-X" || face == "left") {
    hx = ht;
    hy = hu;
    hz = hv;
    pos = G4ThreeVector(-fTank_x - ht, u, v);
  }
  else if (face == "+Y" || face == "back") {
    hx = hu;
    hy = ht;
    hz = hv;
    pos = G4ThreeVector(u, fTank_y + ht, v);
  }
  else if (face == "-Y" || face == "front") {
    hx = hu;
    hy = ht;
    hz = hv;
    pos = G4ThreeVector(u, -fTank_y - ht, v);
  }
  else if (face == "+Z" || face == "top") {
    hx = hu;
    hy = hv;
    hz = ht;
    pos = G4ThreeVector(u, v, fTank_z + ht);
  }
  else if (face == "-Z" || face == "bottom") {
    hx = hu;
    hy = hv;
    hz = ht;
    if (fDimpleEnabled) {
      const G4String sipmMode = GetEffectiveDimpleSiPMMode();

      if (sipmMode == "external") {
        // Same receiving plane as the flat, grease-free control: z = -fTank_z.
        pos = G4ThreeVector(u, v, -fTank_z - ht);
      }
      else if (sipmMode == "surface") {
        const G4double safety = 1.e-6 * mm;
        const G4double rMax = GetSiPMFootprintCornerRadius(u, v, hu, hv);
        const G4double dimpleTopAtFootprint =
          -fTank_z + std::sqrt(fDimpleRadius * fDimpleRadius - rMax * rMax);
        pos = G4ThreeVector(u, v, dimpleTopAtFootprint - ht - safety);
      }
      else if (sipmMode == "opening") {
        pos = G4ThreeVector(u, v, -fTank_z + ht);
      }
      else {
        G4ExceptionDescription msg;
        msg << "Unknown dimple SiPM mode: " << sipmMode
            << ". Use surface, opening, or external.";
        G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                    "OpNovice2_Dimple_011",
                    FatalException,
                    msg);
      }
    }
    else {
      const G4double greaseOffset = fGreaseEnabled ? fGreaseThickness : 0.0;
      pos = G4ThreeVector(u, v, -fTank_z - greaseOffset - ht);
    }
  }
  else if (face == "bottomCavity") {
    hx = hu;
    hy = hv;
    hz = ht;

    if (!fBottomCavityEnabled) {
      G4ExceptionDescription msg;
      msg << "SiPM face bottomCavity requires /opnovice2/tank/bottomCavity true.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_002",
                  FatalException,
                  msg);
    }

    const G4double cavityRadius = GetBottomCavityRadius();
    const G4double rMax = GetSiPMFootprintCornerRadius(u, v, hu, hv);
    const G4double safety = 1.e-6 * mm;

    if (rMax >= cavityRadius - safety) {
      G4ExceptionDescription msg;
      msg << "SiPM footprint does not fit inside the bottom cavity opening. "
          << "farthest corner radius=" << rMax / mm
          << " mm, cavity radius=" << cavityRadius / mm << " mm.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_003",
                  FatalException,
                  msg);
    }

    const G4double cavityTopAtFootprint =
      -fTank_z + std::sqrt(cavityRadius * cavityRadius - rMax * rMax);

    if (fSiPMCavityMode == "surface") {
      pos = G4ThreeVector(u, v, cavityTopAtFootprint - ht - safety);
    }
    else if (fSiPMCavityMode == "opening") {
      const G4double topFace = -fTank_z + fSiPMThickness;
      if (topFace >= cavityTopAtFootprint - safety) {
        G4ExceptionDescription msg;
        msg << "SiPM opening placement would overlap EJ-200. "
            << "top face z=" << topFace / mm
            << " mm, cavity clearance z=" << cavityTopAtFootprint / mm << " mm.";
        G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                    "OpNovice2_SiPM_004",
                    FatalException,
                    msg);
      }
      pos = G4ThreeVector(u, v, -fTank_z + ht);
    }
    else {
      G4ExceptionDescription msg;
      msg << "Unknown SiPM cavity mode: " << fSiPMCavityMode
          << ". Use surface or opening.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_005",
                  FatalException,
                  msg);
    }
  }
  else {
    G4ExceptionDescription msg;
    msg << "Unknown SiPM face: " << face
        << ". Use +X, -X, +Y, -Y, +Z, -Z, bottomCavity.";
    G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                "OpNovice2_SiPM_001",
                FatalException,
                msg);
  }
}

std::vector<G4ThreeVector> DetectorConstruction::GetSiPMLocalPositions() const
{
  if (fSiPMLayout == "edge-two") {
    const G4double offset = 25. * mm;
    return {
      G4ThreeVector(-offset, 0., 0.),
      G4ThreeVector(offset, 0., 0.)
    };
  }
  if (fSiPMLayout == "back-four") {
    const G4double offset = 25. * mm;
    return {
      G4ThreeVector(-offset, -offset, 0.),
      G4ThreeVector(-offset, offset, 0.),
      G4ThreeVector(offset, -offset, 0.),
      G4ThreeVector(offset, offset, 0.)
    };
  }
  if (fSiPMLayout == "back-two") {
    return {G4ThreeVector(-25.*mm, -25.*mm, 0.), G4ThreeVector(25.*mm, 25.*mm, 0.)};
  }
  if (fSiPMLayout == "back-center") return {G4ThreeVector()};
  return {fSiPMLocalPosition};
}

void DetectorConstruction::ValidateSiPMLayout() const
{
  if (fSiPMLayout == "single") {
    return;
  }
  if (fSiPMLayout == "edge-two") {
    if (fSiPMFace != "+X" && fSiPMFace != "right") {
      G4ExceptionDescription msg;
      msg << "The edge-two SiPM layout requires face +X; current face is "
          << fSiPMFace << ".";
      G4Exception("DetectorConstruction::ValidateSiPMLayout",
                  "OpNovice2_SiPM_012",
                  FatalException,
                  msg);
    }
    if (fBottomCavityEnabled || fDimpleEnabled || fGreaseEnabled) {
      G4ExceptionDescription msg;
      msg << "The edge-two SiPM layout supports only the undimpled zero-gap "
          << "coupling proxy; bottom cavity, dimple, and explicit grease must be disabled.";
      G4Exception("DetectorConstruction::ValidateSiPMLayout",
                  "OpNovice2_SiPM_013",
                  FatalException,
                  msg);
    }

    const G4double offset = 25. * mm;
    const G4double safety = 1.e-6 * mm;
    if (offset + 0.5 * fSiPMActiveU >= fTank_y - safety ||
        0.5 * fSiPMActiveV >= fTank_z - safety) {
      G4ExceptionDescription msg;
      msg << "The edge-two SiPM footprints do not fit on the +X tile face. "
          << "tile full side size=" << 2. * fTank_y / mm << " x "
          << 2. * fTank_z / mm << " mm, SiPM active size="
          << fSiPMActiveU / mm << " x " << fSiPMActiveV / mm
          << " mm, center offset=25 mm.";
      G4Exception("DetectorConstruction::ValidateSiPMLayout",
                  "OpNovice2_SiPM_014",
                  FatalException,
                  msg);
    }
    return;
  }
  if ((fSiPMLayout == "back-two" || fSiPMLayout == "back-center") && !IsStackLayoutStudy()) {
    G4Exception("DetectorConstruction::ValidateSiPMLayout", "OpNovice2_LayoutStudy_004",
                FatalException, "back-two and back-center require stack/layoutStudy true.");
  }
  if (fSiPMLayout != "back-four" && fSiPMLayout != "back-two" && fSiPMLayout != "back-center") {
    G4ExceptionDescription msg;
    msg << "Unknown SiPM layout: " << fSiPMLayout
        << ". Use single, edge-two, back-four, back-two, or back-center.";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_007",
                FatalException,
                msg);
    return;
  }

  if (fSiPMFace != "-Z" && fSiPMFace != "bottom") {
    G4ExceptionDescription msg;
    msg << "The back-four SiPM layout requires face -Z; current face is "
        << fSiPMFace << ".";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_008",
                FatalException,
                msg);
  }
  if (fBottomCavityEnabled || fDimpleEnabled || fGreaseEnabled) {
    G4ExceptionDescription msg;
    msg << "The back-four SiPM layout supports only the undimpled zero-gap "
        << "coupling proxy; bottom cavity, dimple, and explicit grease must be disabled.";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_009",
                FatalException,
                msg);
  }

  const G4double offset = fSiPMLayout == "back-center" ? 0. : 25. * mm;
  const G4double safety = 1.e-6 * mm;
  if (offset + 0.5 * fSiPMActiveU >= fTank_x - safety ||
      offset + 0.5 * fSiPMActiveV >= fTank_y - safety) {
    G4ExceptionDescription msg;
    msg << "The back-four SiPM footprints do not fit on the -Z tile face. "
        << "tile full size=" << 2. * fTank_x / mm << " x "
        << 2. * fTank_y / mm << " mm, SiPM active size="
        << fSiPMActiveU / mm << " x " << fSiPMActiveV / mm
        << " mm, center offset=25 mm.";
    G4Exception("DetectorConstruction::ValidateSiPMLayout",
                "OpNovice2_SiPM_010",
                FatalException,
                msg);
  }
}

void DetectorConstruction::SetSiPMLayout(const G4String& layout)
{
  if (layout != "single" && layout != "edge-two" && layout != "back-four" &&
      layout != "back-two" && layout != "back-center") {
    G4ExceptionDescription msg;
    msg << "Invalid SiPM layout: " << layout
        << ". Use single, edge-two, back-four, back-two, or back-center.";
    G4Exception("DetectorConstruction::SetSiPMLayout",
                "OpNovice2_SiPM_011",
                FatalException,
                msg);
  }

  fSiPMLayout = layout;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "SiPM layout set to " << fSiPMLayout << G4endl;
}

void DetectorConstruction::SetSiPMFace(const G4String& face)
{
  fSiPMFace = face;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM face set to " << fSiPMFace << G4endl;
}

void DetectorConstruction::SetSiPMCavityMode(const G4String& mode)
{
  if (mode != "surface" && mode != "opening") {
    G4ExceptionDescription msg;
    msg << "Invalid SiPM cavity mode: " << mode << ". Use surface or opening.";
    G4Exception("DetectorConstruction::SetSiPMCavityMode",
                "OpNovice2_SiPM_006",
                FatalException,
                msg);
  }

  fSiPMCavityMode = mode;
  fSiPMCavityModeExplicit = true;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM cavity mode set to " << fSiPMCavityMode << G4endl;
}

void DetectorConstruction::SetSiPMLocalPosition(const G4ThreeVector& pos)
{
  fSiPMLocalPosition = pos;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM local position set to "
         << fSiPMLocalPosition / cm << " cm" << G4endl;
}

void DetectorConstruction::SetSiPMSize(const G4ThreeVector& size)
{
  fSiPMActiveU = size.x();
  fSiPMActiveV = size.y();
  fSiPMThickness = size.z();

  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM size set to activeU="
         << fSiPMActiveU / mm << " mm, activeV="
         << fSiPMActiveV / mm << " mm, thickness="
         << fSiPMThickness / mm << " mm" << G4endl;
}

void DetectorConstruction::SetGreaseEnabled(G4bool enabled)
{
  fGreaseEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "EJ-550 grease coupling "
         << (fGreaseEnabled ? "enabled" : "disabled") << G4endl;
}

void DetectorConstruction::SetGreaseThickness(G4double thickness)
{
  if (thickness <= 0.0) {
    G4ExceptionDescription msg;
    msg << "Invalid EJ-550 grease thickness: " << thickness / mm
        << " mm. Thickness must be positive.";
    G4Exception("DetectorConstruction::SetGreaseThickness",
                "OpNovice2_Grease_007",
                FatalException,
                msg);
  }

  fGreaseThickness = thickness;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "EJ-550 grease thickness set to "
         << fGreaseThickness / mm << " mm" << G4endl;
}

void DetectorConstruction::SetGreaseSize(const G4ThreeVector& size)
{
  if (size.x() <= 0.0 || size.y() <= 0.0) {
    G4ExceptionDescription msg;
    msg << "Invalid EJ-550 grease size: " << size.x() / mm << " x "
        << size.y() / mm << " mm. Active dimensions must be positive.";
    G4Exception("DetectorConstruction::SetGreaseSize",
                "OpNovice2_Grease_008",
                FatalException,
                msg);
  }

  fGreaseActiveU = size.x();
  fGreaseActiveV = size.y();
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "EJ-550 grease size set to activeU="
         << fGreaseActiveU / mm << " mm, activeV="
         << fGreaseActiveV / mm << " mm" << G4endl;
}
