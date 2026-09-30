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
//
/// \file optical/OpNovice2/OpNovice2.cc
/// \brief Main program of the optical/OpNovice2 example
//

// ConstructProcess follows Geant4 v11.4.2 G4OpticalPhysics.cc registration
// verbatim in order and policy. The sole replacement is the opt-in boundary
// constructor. Legacy/v1 calls the installed implementation directly.
#include "PaintedCornerBoundary.hh"
#include "DetectorConstruction.hh"
#include "OpticalNumerics.hh"
#include "G4Cerenkov.hh"
#include "G4GeneralCerenkov.hh"
#include "G4EmSaturation.hh"
#include "G4LossTableManager.hh"
#include "G4OpAbsorption.hh"
#include "G4OpRayleigh.hh"
#include "G4OpMieHG.hh"
#include "G4OpticalParameters.hh"
#include "G4OpWLS.hh"
#include "G4OpWLS2.hh"
#include "G4ParticleDefinition.hh"
#include "G4ProcessManager.hh"
#include "G4Scintillation.hh"

void StackOpticalPhysics::ConstructProcess()
{
  auto& numerical = OpticalNumerics::Instance();
  numerical.Validate(*fDetector);
  if (numerical.Mode() == "legacy") { G4OpticalPhysics::ConstructProcess(); return; }
  auto* params = G4OpticalParameters::Instance();
  auto* manager = G4OpticalPhoton::OpticalPhoton()->GetProcessManager();
  if (!manager) { OpticalNumerics::Fail("Optical photon has no process manager"); return; }
  if (params->GetProcessActivation("OpAbsorption")) manager->AddDiscreteProcess(new G4OpAbsorption());
  if (params->GetProcessActivation("OpRayleigh")) manager->AddDiscreteProcess(new G4OpRayleigh());
  if (params->GetProcessActivation("OpMieHG")) manager->AddDiscreteProcess(new G4OpMieHG());
  if (params->GetProcessActivation("OpBoundary")) manager->AddDiscreteProcess(new PaintedCornerBoundary(fDetector));
  if (params->GetProcessActivation("OpWLS")) manager->AddDiscreteProcess(new G4OpWLS());
  if (params->GetProcessActivation("OpWLS2")) manager->AddDiscreteProcess(new G4OpWLS2());
  G4VProcess* cerenkov = nullptr;
  if (params->CerenkovGeneral()) cerenkov = new G4GeneralCerenkov();
  else if (params->GetProcessActivation("Cerenkov")) cerenkov = new G4Cerenkov();
  G4Scintillation* scint = nullptr;
  if (params->GetProcessActivation("Scintillation")) {
    scint = new G4Scintillation();
    scint->AddSaturation(G4LossTableManager::Instance()->EmSaturation());
  }
  auto iterator = GetParticleIterator();
  iterator->reset();
  while ((*iterator)()) {
    auto* particle = iterator->value();
    if (particle->IsShortLived()) continue;
    manager = particle->GetProcessManager();
    if (!manager) { OpticalNumerics::Fail("Particle has no process manager"); return; }
    if (cerenkov && cerenkov->IsApplicable(*particle)) manager->AddDiscreteProcess(cerenkov);
    if (scint && scint->IsApplicable(*particle)) {
      manager->AddProcess(scint);
      manager->SetProcessOrderingToLast(scint,idxAtRest);
      manager->SetProcessOrderingToLast(scint,idxPostStep);
    }
  }
}
